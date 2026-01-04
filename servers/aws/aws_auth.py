import os
import yaml
import boto3
import threading
import time
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

_config_cache = {}
_refresh_thread = None

AWS_ACCOUNT_ROLES_FILE = os.environ.get('AWS_ACCOUNT_ROLES_FILE', '/etc/aws/accounts.yaml')

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

def _refresh_credentials(config_path, token_path, aws_dir):
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    with open(token_path, 'r') as f:
        token = f.read().strip()
    
    region = config.get('region', 'us-east-2')
    credentials_path = os.path.join(aws_dir, 'credentials')
    
    credentials_lines = []
    sts_client = boto3.client('sts', region_name=region)
    
    for profile_name, profile_config in config['profiles'].items():
        try:
            response = sts_client.assume_role_with_web_identity(
                RoleArn=profile_config['role_arn'],
                RoleSessionName=f"{profile_name}-mcp-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                WebIdentityToken=token,
                DurationSeconds=3600
            )
            
            creds = response['Credentials']
            
            credentials_lines.append(f'[{profile_name}]')
            credentials_lines.append(f"aws_access_key_id = {creds['AccessKeyId']}")
            credentials_lines.append(f"aws_secret_access_key = {creds['SecretAccessKey']}")
            credentials_lines.append(f"aws_session_token = {creds['SessionToken']}")
            credentials_lines.append('')
            
            logger.info(f"✓ Refreshed credentials for {profile_name}")
        except Exception as e:
            logger.error(f"Failed to refresh credentials for {profile_name}: {e}")
    
    with open(credentials_path, 'w') as f:
        f.write('\n'.join(credentials_lines))
    os.chmod(credentials_path, 0o600)

def _refresh_loop(config_path, token_path, aws_dir, interval_seconds=3000):
    while True:
        try:
            time.sleep(interval_seconds)
            _refresh_credentials(config_path, token_path, aws_dir)
        except Exception as e:
            logger.error(f"Error in credential refresh loop: {e}")

def setup_aws_profiles(
    config_path: str = None,
    token_path: str = "/var/run/secrets/eks.amazonaws.com/serviceaccount/token",
    aws_dir: str = "/root/.aws"
):
    if config_path is None:
        config_path = os.environ.get('AWS_ACCOUNT_ROLES_FILE', '/etc/aws/accounts.yaml')
    global _config_cache, _refresh_thread
    
    with open(config_path, 'r') as f:
        config = yaml.safe_load(f)
    
    if not config or 'profiles' not in config:
        raise ValueError("Invalid config: missing 'profiles' section")
    
    _config_cache = {
        'config_path': config_path,
        'token_path': token_path,
        'aws_dir': aws_dir
    }
    
    with open(token_path, 'r') as f:
        token = f.read().strip()
    
    region = config.get('region', 'us-east-2')
    os.makedirs(aws_dir, exist_ok=True)
    
    credentials_path = os.path.join(aws_dir, 'credentials')
    config_path_out = os.path.join(aws_dir, 'config')
    
    credentials_lines = []
    config_lines = ['[default]', f'region = {region}', '']
    
    sts_client = boto3.client('sts', region_name=region)
    
    for profile_name, profile_config in config['profiles'].items():
        response = sts_client.assume_role_with_web_identity(
            RoleArn=profile_config['role_arn'],
            RoleSessionName=f"{profile_name}-mcp-{datetime.now().strftime('%Y%m%d%H%M%S')}",
            WebIdentityToken=token,
            DurationSeconds=3600
        )
        
        creds = response['Credentials']
        
        credentials_lines.append(f'[{profile_name}]')
        credentials_lines.append(f"aws_access_key_id = {creds['AccessKeyId']}")
        credentials_lines.append(f"aws_secret_access_key = {creds['SecretAccessKey']}")
        credentials_lines.append(f"aws_session_token = {creds['SessionToken']}")
        credentials_lines.append('')
        
        config_lines.append(f'[profile {profile_name}]')
        config_lines.append(f'region = {region}')
        config_lines.append('')
        
        logger.info(f"✓ Set up profile: {profile_name} (account: {profile_config['account_id']})")
    
    with open(credentials_path, 'w') as f:
        f.write('\n'.join(credentials_lines))
    os.chmod(credentials_path, 0o600)
    
    with open(config_path_out, 'w') as f:
        f.write('\n'.join(config_lines))
    
    logger.info(f"Wrote AWS credentials/config to {aws_dir}")
    
    if _refresh_thread is None or not _refresh_thread.is_alive():
        _refresh_thread = threading.Thread(
            target=_refresh_loop,
            args=(config_path, token_path, aws_dir, 3000),
            daemon=True
        )
        _refresh_thread.start()
        logger.info("Started credential refresh thread (refreshes every 50 minutes)")
    
    return list(config['profiles'].keys())
