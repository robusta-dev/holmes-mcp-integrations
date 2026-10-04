import os
import yaml
import boto3
import threading
import time
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

_refresh_thread = None

AWS_ACCOUNT_ROLES_FILE = os.environ.get('AWS_ACCOUNT_ROLES_FILE', '/etc/aws/accounts.yaml')
AWS_REFRESH_CREDENTIALS_SEC = int(os.environ.get('AWS_REFRESH_CREDENTIALS_SEC', '3000'))

def config_file_exists(config_path: str = AWS_ACCOUNT_ROLES_FILE) -> bool:
    return os.path.exists(config_path)

def has_valid_config(config_path: str = AWS_ACCOUNT_ROLES_FILE) -> bool:
    if not config_file_exists(config_path):
        return False
    
    try:
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        
        if not config or 'profiles' not in config:
            return False
        
        return True
    except Exception as e:
        logger.warning(f"Failed to validate config file {config_path}: {e}")
        return False

def _credentials_to_file_lines(profile_name: str, creds: dict) -> list:
    """Convert AWS credentials dict to credentials file format lines."""
    return [
        f'[{profile_name}]',
        f"aws_access_key_id = {creds['AccessKeyId']}",
        f"aws_secret_access_key = {creds['SecretAccessKey']}",
        f"aws_session_token = {creds['SessionToken']}",
        ''
    ]

def _assume_role_with_web_identity(sts_client, profile_name: str, role_arn: str, token: str, duration_seconds: int = 3600) -> dict:
    """Assume IAM role using web identity token and return credentials."""
    response = sts_client.assume_role_with_web_identity(
        RoleArn=role_arn,
        RoleSessionName=f"{profile_name}-mcp-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        WebIdentityToken=token,
        DurationSeconds=duration_seconds
    )
    return response['Credentials']

def _write_credentials_file(credentials_path: str, credentials_lines: list):
    """Write credentials to file with secure permissions (600 = owner read/write only)."""
    with open(credentials_path, 'w') as f:
        f.write('\n'.join(credentials_lines))
    # chmod 0o600 = rw------- (owner read/write, no access for group/others)
    # This is a security best practice for credentials files
    os.chmod(credentials_path, 0o600)

def _write_config_file(config_path: str, default_region: str, profile_regions: dict):
    """
    Write AWS config file with per-profile region settings.
    
    The config file is different from the credentials file:
    - Credentials file (~/.aws/credentials): Contains actual AWS credentials (access keys, secrets, tokens)
      that expire and need to be refreshed periodically (every ~50 minutes)
    - Config file (~/.aws/config): Contains static configuration like region settings, output format, etc.
      This doesn't change unless profiles are added/removed, so it only needs to be written once during setup.
    
    Args:
        config_path: Path where config file will be written
        default_region: Default region for [default] profile
        profile_regions: Dict mapping profile names to their regions
    """
    config_lines = ['[default]', f'region = {default_region}', '']
    
    # Add per-profile region configurations
    for profile_name, profile_region in profile_regions.items():
        config_lines.append(f'[profile {profile_name}]')
        config_lines.append(f'region = {profile_region}')
        config_lines.append('')
    
    with open(config_path, 'w') as f:
        f.write('\n'.join(config_lines))

def _process_profiles_to_credentials(config: dict, token: str, default_region: str) -> tuple:
    """
    Process all profiles from config and return credentials file lines and profile regions.
    
    Args:
        config: Configuration dict with 'profiles' key
        token: Web identity token for assume_role_with_web_identity
        default_region: Default region to use if profile doesn't specify one
    
    Returns:
        Tuple of (credentials_lines, profile_regions_dict)
    
    Raises:
        Exception: If any profile fails to process, logs the problematic profile and raises
    """
    credentials_lines = []
    profile_regions = {}
    
    for profile_name, profile_config in config['profiles'].items():
        try:
            # Use profile-specific region if provided, otherwise use default
            profile_region = profile_config.get('region', default_region)
            profile_regions[profile_name] = profile_region
            
            # Create STS client with profile's region (STS is global but good practice)
            sts_client = boto3.client('sts', region_name=profile_region)
            
            creds = _assume_role_with_web_identity(
                sts_client, profile_name, profile_config['role_arn'], token
            )
            credential_file_lines = _credentials_to_file_lines(profile_name, creds)
            credentials_lines.extend(credential_file_lines)
            
            account_id = profile_config.get('account_id')
            if not account_id:
                raise Exception(f"Missing 'account_id' in profile configuration {profile_name}")
            logger.info(f"✓ Processed profile: {profile_name} (account: {account_id}, region: {profile_region})")
        except Exception as e:
            logger.error(f"Failed to process profile '{profile_name}': {e}", exc_info=True)
            raise
    
    return credentials_lines, profile_regions

def _refresh_credentials(config_path: str, token_path: str, aws_dir: str) -> dict:
    """
    Refresh credentials by reading config/token, processing profiles, and writing credentials file.
    
    Returns:
        Dictionary mapping profile names to their regions
    
    Raises:
        Exception: If any profile fails to process
    """
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    with open(token_path, 'r') as f:
        token = f.read().strip()
    
    default_region = config.get('region', 'us-east-2')
    credentials_path = os.path.join(aws_dir, 'credentials')
    
    credentials_lines, profile_regions = _process_profiles_to_credentials(config, token, default_region)
    _write_credentials_file(credentials_path, credentials_lines)
    
    return profile_regions

def _refresh_loop(config_path, token_path, aws_dir):
    """
    Background thread loop that refreshes credentials periodically.
    Refresh interval is controlled by AWS_REFRESH_CREDENTIALS_SEC environment variable (default: 3000 seconds).
    If refresh fails, logs error and continues loop (will retry on next interval).
    """
    while True:
        try:
            time.sleep(AWS_REFRESH_CREDENTIALS_SEC)
            _refresh_credentials(config_path, token_path, aws_dir)
        except Exception as e:
            logger.error(f"Error in credential refresh loop (will retry on next interval): {e}", exc_info=True)

def setup_aws_profiles(
    config_path: str = AWS_ACCOUNT_ROLES_FILE,
    token_path: str = "/var/run/secrets/eks.amazonaws.com/serviceaccount/token",
    aws_dir: str = "/root/.aws"
):
    """
    Set up AWS profiles by refreshing credentials and starting background refresh thread.
    
    _refresh_thread is a module-level global variable to track the background refresh thread.
    It needs to be global because setup_aws_profiles may be called multiple times, and we need
    to ensure only one refresh thread is running at a time.
    """
    if not config_file_exists():
        logger.info("No custom aws profile file found")
        return
    elif not has_valid_config():
        logger.error(f"Custom config file {AWS_ACCOUNT_ROLES_FILE} invalid format, skipping profile setup")
        return
    
    global _refresh_thread
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    if not config or 'profiles' not in config:
        raise ValueError("Invalid config: missing 'profiles' section")
    
    os.makedirs(aws_dir, exist_ok=True)
    
    # Refresh credentials (writes credentials file and returns profile regions)
    profile_regions = _refresh_credentials(config_path, token_path, aws_dir)
    
    # Write config file with per-profile regions (only needs to be done once during setup)
    default_region = config.get('region', 'us-east-2')
    config_path_out = os.path.join(aws_dir, 'config')
    _write_config_file(config_path_out, default_region, profile_regions)
    
    logger.info(f"Wrote AWS credentials/config to {aws_dir}")
    
    if _refresh_thread is None or not _refresh_thread.is_alive():
        _refresh_thread = threading.Thread(
            target=_refresh_loop,
            args=(config_path, token_path, aws_dir),
            daemon=True
        )
        _refresh_thread.start()
        logger.info(f"Started credential refresh thread (interval: {AWS_REFRESH_CREDENTIALS_SEC} seconds)")

    profiles = list(config['profiles'].keys())
    logger.info(f"Set up {len(profiles)} AWS profiles: {', '.join(profiles)}")

    return