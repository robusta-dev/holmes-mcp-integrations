"""Multi-account AWS authentication using EKS OIDC tokens."""

import os
import json
import yaml
import boto3
from datetime import datetime, timezone, timedelta
from typing import Dict, Optional, Any
import logging

logger = logging.getLogger(__name__)

class AWSMultiAccountAuth:
    def __init__(
        self,
        config_path: str = "/etc/aws/accounts.yaml",
        token_path: str = "/var/run/secrets/eks.amazonaws.com/serviceaccount/token"
    ):
        self.config_path = config_path
        self.token_path = token_path
        self.config = None
        self.credentials_cache: Dict[str, Dict] = {}
        self.refresh_threshold = timedelta(minutes=5)
        
        self._load_config()
    
    def _load_config(self):
        """Load accounts configuration."""
        try:
            with open(self.config_path, 'r') as f:
                self.config = yaml.safe_load(f)
            
            if not self.config or 'profiles' not in self.config:
                raise ValueError("Invalid config: missing 'profiles' section")
            
            logger.info(f"Loaded {len(self.config['profiles'])} profiles: {', '.join(self.config['profiles'].keys())}")
            
        except Exception as e:
            logger.error(f"Failed to load config from {self.config_path}: {e}")
            raise
    
    def _get_oidc_token(self) -> str:
        """Read the EKS ServiceAccount OIDC token."""
        try:
            with open(self.token_path, 'r') as f:
                token = f.read().strip()
                logger.debug(f"Read OIDC token ({len(token)} characters)")
                return token
        except Exception as e:
            logger.error(f"Failed to read OIDC token from {self.token_path}: {e}")
            raise
    
    def _needs_refresh(self, cached_creds: Dict) -> bool:
        """Check if cached credentials need refresh."""
        if not cached_creds or 'Expiration' not in cached_creds:
            return True
        
        expiration = cached_creds['Expiration']
        if isinstance(expiration, str):
            expiration = datetime.fromisoformat(expiration.replace('Z', '+00:00'))
        
        time_until_expiry = expiration - datetime.now(timezone.utc)
        needs_refresh = time_until_expiry < self.refresh_threshold
        
        if needs_refresh:
            logger.debug(f"Credentials need refresh (expires in {time_until_expiry})")
        
        return needs_refresh
    
    def get_credentials(self, profile_name: str) -> Dict:
        """Get AWS credentials for a specific profile."""
        if not self.config:
            raise ValueError("Configuration not loaded")
        
        profile = self.config['profiles'].get(profile_name)
        if not profile:
            available = ', '.join(self.config['profiles'].keys())
            raise ValueError(f"Profile '{profile_name}' not found. Available: {available}")
        
        # Check cache
        cached = self.credentials_cache.get(profile_name)
        if cached and not self._needs_refresh(cached):
            logger.debug(f"Using cached credentials for {profile_name}")
            return cached
        
        # Get fresh credentials
        logger.info(f"Refreshing credentials for {profile_name} (account: {profile['account_id']})")
        
        try:
            token = self._get_oidc_token()
            region = self.config.get('region', 'us-east-2')
            
            # Create STS client with no credentials (will use OIDC token)
            sts_client = boto3.client('sts', region_name=region)
            
            # Assume role with web identity
            response = sts_client.assume_role_with_web_identity(
                RoleArn=profile['role_arn'],
                RoleSessionName=f"{profile_name}-mcp-{datetime.now().strftime('%Y%m%d%H%M%S')}",
                WebIdentityToken=token,
                DurationSeconds=3600
            )
            
            if 'Credentials' not in response:
                raise Exception("No credentials returned from STS")
            
            creds = response['Credentials']
            
            # Convert datetime to string for JSON serialization
            if isinstance(creds['Expiration'], datetime):
                creds['Expiration'] = creds['Expiration'].isoformat()
            
            # Cache credentials
            self.credentials_cache[profile_name] = creds
            
            logger.info(f"✓ Successfully authenticated to {profile_name} (expires: {creds['Expiration']})")
            
            return creds
            
        except Exception as e:
            logger.error(f"Failed to authenticate to {profile_name}: {e}")
            # Remove failed credentials from cache
            self.credentials_cache.pop(profile_name, None)
            raise
    
    def get_aws_env_vars(self, profile_name: str) -> Dict[str, str]:
        """Get AWS environment variables for a specific profile."""
        creds = self.get_credentials(profile_name)
        
        return {
            'AWS_ACCESS_KEY_ID': creds['AccessKeyId'],
            'AWS_SECRET_ACCESS_KEY': creds['SecretAccessKey'],
            'AWS_SESSION_TOKEN': creds['SessionToken'],
            'AWS_DEFAULT_REGION': self.config.get('region', 'us-east-2'),
            'AWS_REGION': self.config.get('region', 'us-east-2')
        }
    
    def list_profiles(self) -> list:
        """Get list of available profiles."""
        if not self.config:
            return []
        return list(self.config['profiles'].keys())
    
    def test_auth(self, profile_name: str) -> Dict[str, Any]:
        """Test authentication for a profile."""
        try:
            creds = self.get_credentials(profile_name)
            
            # Create a temporary session to test
            session = boto3.Session(
                aws_access_key_id=creds['AccessKeyId'],
                aws_secret_access_key=creds['SecretAccessKey'],
                aws_session_token=creds['SessionToken'],
                region_name=self.config.get('region', 'us-east-2')
            )
            
            sts = session.client('sts')
            identity = sts.get_caller_identity()
            
            return {
                'success': True,
                'profile': profile_name,
                'account': identity.get('Account'),
                'arn': identity.get('Arn'),
                'user_id': identity.get('UserId'),
                'expires': creds.get('Expiration'),
                'account_id': self.config['profiles'][profile_name]['account_id']
            }
            
        except Exception as e:
            return {
                'success': False,
                'profile': profile_name,
                'error': str(e)
            }
