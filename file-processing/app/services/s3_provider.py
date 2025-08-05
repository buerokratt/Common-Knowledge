import boto3
import os
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple, Callable
from botocore.exceptions import ClientError, NoCredentialsError
from app.services.blob_storage import BlobStorageProvider, BlobStorageException
from app.core.config import settings
import botocore.session
import logging

logger = logging.getLogger(__name__)

session = botocore.session.get_session()
session.set_config_variable('s3', {'signature_version': 's3v4'})

class S3Provider(BlobStorageProvider):
    def __init__(self):
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
            config=boto3.session.Config(signature_version='s3v4')
        )
        self.bucket_name = settings.s3_bucket_name

    def upload_file_content(self, file_content: bytes, destination_path: str, content_type: str = "application/octet-stream") -> str:
        """Upload file content directly to S3.
        
        Args:
            file_content: Raw file content as bytes
            destination_path: S3 destination path
            content_type: MIME type of the file
            
        Returns:
            str: S3 URI of uploaded file
        """
        try:
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=destination_path,
                Body=file_content,
                ContentType=content_type
            )
            return f"s3://{self.bucket_name}/{destination_path}"
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"S3 upload failed: {str(e)}")
        except Exception as e:
            raise BlobStorageException(f"Upload failed: {str(e)}")

    def upload_file(self, source_file_path: str, destination_path: str) -> str:
        try:
            if not os.path.exists(source_file_path):
                raise BlobStorageException(f"Source file not found: {source_file_path}")

            self.s3_client.upload_file(
                source_file_path,
                self.bucket_name,
                destination_path
            )
            return f"s3://{self.bucket_name}/{destination_path}"
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"S3 upload failed: {str(e)}")
        except Exception as e:
            raise BlobStorageException(f"Upload failed: {str(e)}")

    def generate_download_url(self, path: str) -> tuple[str, datetime]:
        try:
            if not self.file_exists(path):
                raise BlobStorageException(f"File not found in blob storage: {path}")

            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': path
                },
                ExpiresIn=settings.s3_presigned_url_expiration
            )
            expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=settings.s3_presigned_url_expiration)

            return url, expires_at
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"Failed to generate download URL: {str(e)}")
        except Exception as e:
            raise BlobStorageException(f"Failed to generate download URL: {str(e)}")

    def file_exists(self, path: str) -> bool:
        try:
            self.s3_client.head_object(Bucket=self.bucket_name, Key=path)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == '404':
                return False
            raise BlobStorageException(f"Error checking file existence: {str(e)}")
        except Exception as e:
            raise BlobStorageException(f"Error checking file existence: {str(e)}")

    def download_file(self, s3_key: str, local_file_path: str) -> bool:
        """Download a file from S3 to local filesystem."""
        try:
            # Ensure the local directory exists
            os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
            
            # Download the file
            self.s3_client.download_file(
                self.bucket_name,
                s3_key,
                local_file_path
            )
            
            return os.path.exists(local_file_path)
                
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'NoSuchKey':
                raise BlobStorageException(f"File not found in S3: {s3_key}")
            else:
                raise BlobStorageException(f"S3 download failed: {str(e)}")
        except Exception as e:
            raise BlobStorageException(f"Download failed: {str(e)}")

    def list_folder_files(self, s3_prefix: str) -> List[Tuple[str, int]]:
        """List all files in an S3 folder/prefix.
        
        Args:
            s3_prefix: S3 prefix/folder path
            
        Returns:
            List of tuples containing (file_key, file_size)
        """
        try:
            files = []
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            # Ensure prefix ends with / for proper folder listing
            if s3_prefix and not s3_prefix.endswith('/'):
                s3_prefix += '/'
            
            page_iterator = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix=s3_prefix
            )
            
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        # Skip folder markers, only include actual files
                        if not obj['Key'].endswith('/'):
                            files.append((obj['Key'], obj['Size']))
            
            return files
            
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"Failed to list folder contents: {str(e)}")
        except Exception as e:
            raise BlobStorageException(f"Failed to list folder contents: {str(e)}")

    def download_folder(self, s3_prefix: str, local_folder_path: str, 
                       exclusion_filter: Optional[Callable[[str], bool]] = None) -> Tuple[int, int, List[Tuple[str, str, bool, Optional[str]]], List[str]]:
        """Download all files from an S3 folder to local filesystem with optional exclusion filter.
        
        Args:
            s3_prefix: S3 prefix/folder path
            local_folder_path: Local folder where files should be saved
            exclusion_filter: Optional function that takes relative path and returns True if file should be excluded
            
        Returns:
            Tuple of (successful_count, failed_count, results, excluded_paths)
            Results is list of (s3_key, local_path, success, error_message)
            Excluded_paths is list of relative paths that were excluded
        """
        try:
            # List all objects (files AND folder markers) in the folder
            all_objects = []
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            # Ensure prefix ends with / for proper folder listing
            clean_prefix = s3_prefix
            if clean_prefix and not clean_prefix.endswith('/'):
                clean_prefix += '/'
            
            page_iterator = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix=clean_prefix
            )
            
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        all_objects.append((obj['Key'], obj['Size'], obj['LastModified']))
            
            if not all_objects:
                return 0, 0, [], []
            
            successful_downloads = 0
            failed_downloads = 0
            results = []
            excluded_paths = []
            
            # Keep track of all parent directories that should exist
            # even if their contents are excluded
            parent_dirs_to_create = set()
            
            # First pass: collect all parent directories from files
            for s3_key, file_size, last_modified in all_objects:
                relative_path = s3_key[len(clean_prefix):] if clean_prefix else s3_key
                
                # Add parent directories to the set (for files only, not folder markers)
                if not s3_key.endswith('/'):
                    path_parts = relative_path.split('/')
                    for i in range(len(path_parts) - 1):  # Exclude filename
                        parent_dir = '/'.join(path_parts[:i+1])
                        if parent_dir:
                            parent_dirs_to_create.add(parent_dir)
            
            # Second pass: process all objects
            for s3_key, file_size, last_modified in all_objects:
                try:
                    # Calculate relative path by removing the prefix
                    relative_path = s3_key[len(clean_prefix):] if clean_prefix else s3_key
                    
                    # Check if this object should be excluded
                    if exclusion_filter and exclusion_filter(relative_path):
                        excluded_paths.append(relative_path)
                        continue
                    
                    # Handle folder markers (empty folders)
                    if s3_key.endswith('/'):
                        # This is an empty folder marker
                        folder_rel_path = relative_path.rstrip('/')
                        local_folder_full = os.path.join(local_folder_path, folder_rel_path)
                        
                        # Create the empty folder
                        os.makedirs(local_folder_full, exist_ok=True)
                        
                        # Set the folder's modification time to match S3
                        if os.path.exists(local_folder_full):
                            timestamp = last_modified.timestamp()
                            os.utime(local_folder_full, (timestamp, timestamp))
                            results.append((s3_key, local_folder_full, True, None))
                            successful_downloads += 1
                        else:
                            results.append((s3_key, local_folder_full, False, "Folder not created"))
                            failed_downloads += 1
                            results.append((s3_key, local_folder_full, False, "Folder not created"))
                            failed_downloads += 1
                    else:
                        # This is a regular file
                        local_file_path = os.path.join(local_folder_path, relative_path)
                        
                        # Ensure local directory exists
                        os.makedirs(os.path.dirname(local_file_path), exist_ok=True)
                        
                        # Download the file
                        self.s3_client.download_file(
                            self.bucket_name,
                            s3_key,
                            local_file_path
                        )
                        
                        # Set the file's modification time to match S3
                        if os.path.exists(local_file_path):
                            # Convert datetime to timestamp
                            timestamp = last_modified.timestamp()
                            os.utime(local_file_path, (timestamp, timestamp))
                            
                            results.append((s3_key, local_file_path, True, None))
                            successful_downloads += 1
                        else:
                            results.append((s3_key, local_file_path, False, "File not found after download"))
                            failed_downloads += 1
                        
                except Exception as e:
                    error_msg = f"Failed to process {s3_key}: {str(e)}"
                    local_path = os.path.join(local_folder_path, s3_key[len(clean_prefix):] if clean_prefix else s3_key)
                    results.append((s3_key, local_path, False, error_msg))
                    failed_downloads += 1
            
            # Third pass: create any parent directories that became empty due to exclusions
            for parent_dir in parent_dirs_to_create:
                if not (exclusion_filter and exclusion_filter(parent_dir)):
                    local_parent_path = os.path.join(local_folder_path, parent_dir)
                    if not os.path.exists(local_parent_path):
                        try:
                            os.makedirs(local_parent_path, exist_ok=True)
                            # Don't count these in successful_downloads as they're implicit
                        except Exception as e:
                            # Log but don't fail for directory creation issues
                            pass
            
            return successful_downloads, failed_downloads, results, excluded_paths
            
        except Exception as e:
            raise BlobStorageException(f"Failed to download folder: {str(e)}")

    def clean_path(self, path: str) -> str:
        clean_path = path
        if path.startswith('s3://'):
            # Extract key from s3://bucket/key format
            parts = path.replace('s3://', '').split('/', 1)
            if len(parts) > 1:
                clean_path = parts[1]
            else:
                clean_path = parts[0]
        return clean_path

    def generate_upload_urls(self, paths: List[str], content_type: Optional[str] = None, expires_in: Optional[int] = None) -> List[tuple[str, str, datetime]]:
        """Generate presigned upload URLs for multiple blob paths.
        
        Args:
            paths: List of blob paths to generate upload URLs for
            content_type: Optional content type for the upload
            expires_in: Optional expiration time in seconds
            
        Returns:
            List of tuples containing (path, upload_url, expires_at)
        """
        try:
            expiration_seconds = expires_in or settings.s3_presigned_url_expiration
            expires_at = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=expiration_seconds)
            
            upload_urls = []
            
            for path in paths:
                # Clean the blob_path - remove any s3:// prefix if present
                clean_path = self.clean_path(path)
                
                params = {
                    'Bucket': self.bucket_name,
                    'Key': clean_path
                }
                
                # Add content type if provided
                if content_type:
                    params['ContentType'] = content_type
                
                url = self.s3_client.generate_presigned_url(
                    'put_object',
                    Params=params,
                    ExpiresIn=expiration_seconds
                )
                
                upload_urls.append((path, url, expires_at))
            
            return upload_urls
            
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"Failed to generate upload URLs: {str(e)}")
        except Exception as e:
            raise BlobStorageException(f"Failed to generate upload URLs: {str(e)}")

    def move_file(self, source_path: str, destination_path: str) -> bool:
        """Move a file from source to destination within S3.
        
        Args:
            source_path: Source file path in S3
            destination_path: Destination file path in S3
            
        Returns:
            bool: True if move was successful, False otherwise
        """
        try:
            # Check if source file exists
            if not self.file_exists(source_path):
                raise BlobStorageException(f"Source file not found: {source_path}")
            
            # Copy the object to the new location
            copy_source = {
                'Bucket': self.bucket_name,
                'Key': source_path
            }
            
            self.s3_client.copy_object(
                CopySource=copy_source,
                Bucket=self.bucket_name,
                Key=destination_path
            )
            
            # Verify the copy was successful
            if not self.file_exists(destination_path):
                raise BlobStorageException("File copy verification failed")
            
            # Delete the source file
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=source_path
            )
            
            # Verify the source file was deleted
            if self.file_exists(source_path):
                # Attempt to clean up the destination if source deletion failed
                try:
                    self.s3_client.delete_object(
                        Bucket=self.bucket_name,
                        Key=destination_path
                    )
                except:
                    pass  # Ignore cleanup errors
                raise BlobStorageException("Source file deletion failed")
            
            return True
            
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"S3 move operation failed: {str(e)}")
        except BlobStorageException:
            raise  # Re-raise blob storage exceptions
        except Exception as e:
            raise BlobStorageException(f"Move operation failed: {str(e)}")
    
    def move_folder(self, source_prefix: str, destination_prefix: str) -> bool:
        """Move a folder from source to destination within S3.
        
        Args:
            source_prefix: Source folder prefix in S3 (should end with /)
            destination_prefix: Destination folder prefix in S3 (should end with /)
            
        Returns:
            bool: True if move was successful, False otherwise
        """
        try:
            # List all objects in the source folder
            all_objects = []
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            page_iterator = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix=source_prefix
            )
            
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        all_objects.append(obj['Key'])
            
            if not all_objects:
                # Empty folder or doesn't exist - create destination folder marker if needed
                if not destination_prefix.endswith('/'):
                    destination_prefix += '/'
                
                # Create empty folder marker at destination
                self.s3_client.put_object(
                    Bucket=self.bucket_name,
                    Key=destination_prefix,
                    Body=b''
                )
                return True
            
            # Track successful moves for rollback if needed
            moved_objects = []
            
            try:
                # Move each object
                for source_key in all_objects:
                    # Calculate destination key
                    relative_path = source_key[len(source_prefix):]
                    destination_key = destination_prefix + relative_path
                    
                    # Copy object to new location
                    copy_source = {
                        'Bucket': self.bucket_name,
                        'Key': source_key
                    }
                    
                    self.s3_client.copy_object(
                        CopySource=copy_source,
                        Bucket=self.bucket_name,
                        Key=destination_key
                    )
                    
                    # Verify copy was successful
                    if not self.file_exists(destination_key):
                        raise BlobStorageException(f"Copy verification failed for {source_key}")
                    
                    moved_objects.append((source_key, destination_key))
                
                # If all copies successful, delete source objects
                for source_key, _ in moved_objects:
                    self.s3_client.delete_object(
                        Bucket=self.bucket_name,
                        Key=source_key
                    )
                    
                    # Verify deletion (optional - S3 delete is usually reliable)
                    if self.file_exists(source_key):
                        logger.warning(f"Source object {source_key} still exists after deletion")
                
                # Clean up empty folder markers in the source path
                self._cleanup_empty_folders(source_prefix)
                
                return True
                
            except Exception as e:
                # Rollback: delete any successfully copied objects
                logger.error(f"Folder move failed, attempting rollback: {str(e)}")
                for _, destination_key in moved_objects:
                    try:
                        self.s3_client.delete_object(
                            Bucket=self.bucket_name,
                            Key=destination_key
                        )
                    except Exception as rollback_error:
                        logger.error(f"Rollback failed for {destination_key}: {str(rollback_error)}")
                
                raise BlobStorageException(f"Folder move failed: {str(e)}")
                
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"S3 folder move operation failed: {str(e)}")
        except BlobStorageException:
            raise  # Re-raise blob storage exceptions
        except Exception as e:
            raise BlobStorageException(f"Folder move operation failed: {str(e)}")

    def delete_file(self, path: str) -> bool:
        """Delete a file from S3.
        
        Args:
            path: File path in S3 to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            # Check if file exists before attempting deletion
            if not self.file_exists(path):
                raise BlobStorageException(f"File not found: {path}")
            
            # Delete the file
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=path
            )
            
            # Verify the file was deleted
            if self.file_exists(path):
                raise BlobStorageException("File deletion verification failed")
            
            return True
            
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"S3 delete operation failed: {str(e)}")
        except BlobStorageException:
            raise  # Re-raise blob storage exceptions
        except Exception as e:
            raise BlobStorageException(f"Delete operation failed: {str(e)}")

    def delete_folder(self, prefix: str) -> bool:
        """Delete a folder and all its contents from S3.
        
        Args:
            prefix: Folder prefix in S3 (should end with /)
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            # Ensure prefix ends with / for proper folder handling
            if not prefix.endswith('/'):
                prefix += '/'
            
            # List all objects in the folder
            all_objects = []
            paginator = self.s3_client.get_paginator('list_objects_v2')
            
            page_iterator = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix=prefix
            )
            
            for page in page_iterator:
                if 'Contents' in page:
                    for obj in page['Contents']:
                        all_objects.append({'Key': obj['Key']})
            
            if not all_objects:
                # No objects found - folder doesn't exist or is already empty
                logger.info(f"No objects found for prefix {prefix}")
                return True
            
            # Delete objects in batches (S3 allows up to 1000 objects per batch)
            batch_size = 1000
            deleted_count = 0
            
            for i in range(0, len(all_objects), batch_size):
                batch = all_objects[i:i + batch_size]
                
                response = self.s3_client.delete_objects(
                    Bucket=self.bucket_name,
                    Delete={
                        'Objects': batch,
                        'Quiet': False  # Return info about deleted objects
                    }
                )
                
                # Check for any errors in the batch deletion
                if 'Errors' in response and response['Errors']:
                    error_messages = []
                    for error in response['Errors']:
                        error_messages.append(f"Key: {error['Key']}, Code: {error['Code']}, Message: {error['Message']}")
                    raise BlobStorageException(f"Batch delete errors: {'; '.join(error_messages)}")
                
                # Count successfully deleted objects
                if 'Deleted' in response:
                    deleted_count += len(response['Deleted'])
            
            logger.info(f"Successfully deleted {deleted_count} objects from folder {prefix}")
            
            # Verify deletion by checking if any objects still exist with this prefix
            verify_response = self.s3_client.list_objects_v2(
                Bucket=self.bucket_name,
                Prefix=prefix,
                MaxKeys=1
            )
            
            if 'Contents' in verify_response and len(verify_response['Contents']) > 0:
                remaining_objects = [obj['Key'] for obj in verify_response['Contents']]
                raise BlobStorageException(f"Deletion verification failed. Remaining objects: {remaining_objects}")
            
            return True
            
        except NoCredentialsError:
            raise BlobStorageException("AWS credentials not found")
        except ClientError as e:
            raise BlobStorageException(f"S3 folder delete operation failed: {str(e)}")
        except BlobStorageException:
            raise  # Re-raise blob storage exceptions
        except Exception as e:
            raise BlobStorageException(f"Folder delete operation failed: {str(e)}")
        
    def _cleanup_empty_folders(self, folder_prefix: str) -> None:
        """Clean up empty folder markers after moving folder contents.
        
        Args:
            folder_prefix: The folder prefix that was moved from
        """
        try:
            # Remove trailing slash for processing
            clean_prefix = folder_prefix.rstrip('/')
            
            # Work backwards through the path hierarchy
            path_parts = clean_prefix.split('/')
            
            for i in range(len(path_parts), 0, -1):
                current_path = '/'.join(path_parts[:i])
                folder_marker = current_path + '/'
                
                # Check if this folder marker exists
                try:
                    self.s3_client.head_object(Bucket=self.bucket_name, Key=folder_marker)
                    
                    # Check if the folder is now empty (no other objects with this prefix)
                    response = self.s3_client.list_objects_v2(
                        Bucket=self.bucket_name,
                        Prefix=folder_marker,
                        MaxKeys=1
                    )
                    
                    # If no contents found, delete the empty folder marker
                    if 'Contents' not in response or len(response['Contents']) == 0:
                        self.s3_client.delete_object(
                            Bucket=self.bucket_name,
                            Key=folder_marker
                        )
                        logger.info(f"Cleaned up empty folder marker: {folder_marker}")
                    else:
                        # If folder still has contents, stop going up the hierarchy
                        break
                        
                except ClientError as e:
                    if e.response['Error']['Code'] == '404':
                        # Folder marker doesn't exist, continue
                        continue
                    else:
                        # Other error, log but don't fail the overall operation
                        logger.warning(f"Error checking folder marker {folder_marker}: {str(e)}")
                        continue
                        
        except Exception as e:
            # Log error but don't fail the overall move operation
            logger.warning(f"Error during empty folder cleanup: {str(e)}")

s3_provider = S3Provider()