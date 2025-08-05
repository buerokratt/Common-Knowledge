import { apiDev } from './api';

export interface ApiResponse {
  response: any;
}

// S3 Upload interfaces
export interface UploadUrlItem {
  path: string;
  uploadUrl: string;
  expiresIn?: string;
  sourceFileId: string;
  fileName: string;
}

export interface CreateSourceWithFilesResponse {
  sourceId: string;
  fileUploadUrls: UploadUrlItem[];
}

export interface RegisterUploadedFileRequest {
  base_id: string;
  file_name: string;
  subsector: string;
  original_data_url: string;
}

export interface RegisterUploadedFilesRequest {
  sourceId: string;
  files: RegisterUploadedFileRequest[];
}

// Upload URLs interfaces (for file editing) - Updated to match backend
export interface FileUploadInfo {
  path: string;
  content_type?: string;
}

export interface GetUploadUrlsRequest {
  files: FileUploadInfo[];
  expires_in?: number;
}

export interface UploadUrlResponse {
  path: string;
  upload_url: string;
  expires_at: string;
}

export interface GetUploadUrlsResponse {
  upload_urls: UploadUrlResponse[];
}

// Progress callback types
export type FileProgressCallback = (
  fileIndex: number,
  fileName: string,
  status: 'uploading' | 'success' | 'error'
) => void;

// Track uploaded files for registration
export interface UploadedFileInfo {
  uploadItem: UploadUrlItem;
  file: File;
  subsector: string;
  index: number;
}

/**
 * Create source and get S3 upload URLs for new files
 */
export const createSourceWithFiles = async (
  agencyBaseId: string,
  subsector: string,
  files: File[]
): Promise<CreateSourceWithFilesResponse> => {
  // Create file metadata with names and content types
  const fileData = files.map((file) => ({
    name: file.name,
    contentType: file.type || 'application/octet-stream',
  }));

  const response = await apiDev.post(
    '/source/file/create-source-if-not-exists-and-upload-urls',
    {
      agencyBaseId: agencyBaseId,
      subsector: subsector,
      files: fileData,
    }
  );

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response;
};

/**
 * Get upload URLs for existing file paths (for editing)
 */
export const getUploadUrls = async (
  request: GetUploadUrlsRequest
): Promise<GetUploadUrlsResponse> => {
  const response = await apiDev.post('/source-file/get-upload-urls', {
    files: request.files,
    expires_in: request.expires_in || 3600,
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response || apiResponse;
};

/**
 * Upload file to S3 using pre-signed URL
 */
export const uploadFileToS3 = async (
  uploadUrl: string,
  file: File
): Promise<void> => {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();

    xhr.addEventListener('load', () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        console.error(
          'S3 Upload failed:',
          xhr.status,
          xhr.statusText,
          xhr.responseText
        );
        reject(
          new Error(
            `Upload failed with status: ${xhr.status} - ${xhr.statusText}`
          )
        );
      }
    });

    xhr.addEventListener('error', () => {
      console.error('S3 Upload error:', xhr.statusText);
      reject(new Error('Upload failed due to network error'));
    });

    xhr.open('PUT', uploadUrl);
    xhr.setRequestHeader(
      'Content-Type',
      file.type || 'application/octet-stream'
    );
    xhr.send(file);
  });
};

/**
 * Upload text content to S3 using pre-signed URL
 */
export const uploadContentToS3 = async (
  uploadUrl: string,
  content: string,
  contentType: string = 'text/plain'
): Promise<void> => {
  const response = await fetch(uploadUrl, {
    method: 'PUT',
    headers: {
      'Content-Type': contentType,
    },
    body: content,
  });

  if (!response.ok) {
    throw new Error(`Upload failed: ${response.statusText}`);
  }
};

/**
 * Register uploaded files in database
 */
export const registerUploadedFiles = async (
  agencyId: string,
  sourceId: string,
  files: RegisterUploadedFileRequest[]
): Promise<void> => {
  const response = await apiDev.post('/source-file/add-uploaded-files', {
    agencyId: agencyId,
    sourceId: sourceId,
    files: files,
  });

  const apiResponse: ApiResponse = response.data;

  // Check if registration was successful
  if (response.status >= 400) {
    throw new Error(
      `Failed to register uploaded files: ${
        apiResponse.error || 'Unknown error'
      }`
    );
  }
};

/**
 * Upload multiple files to S3 with progress tracking
 */
export const uploadFilesToS3WithProgress = async (
  uploadItems: UploadUrlItem[],
  files: File[],
  onFileProgress?: FileProgressCallback
): Promise<UploadedFileInfo[]> => {
  const successfulUploads: UploadedFileInfo[] = [];

  for (let index = 0; index < uploadItems.length; index++) {
    const uploadItem = uploadItems[index];
    const file = files[index];

    if (!file) {
      console.warn(`No file found at index ${index}`);
      continue;
    }

    try {
      // Notify progress callback that this file is starting upload
      onFileProgress?.(index, file.name, 'uploading');

      // Decode HTML entities in the upload URL
      const decodedUploadUrl = uploadItem.uploadUrl
        .replace(/&amp;/g, '&')
        .replace(/&#x3D;/g, '=');

      // Upload file
      await uploadFileToS3(decodedUploadUrl, file);

      // Track successful upload
      successfulUploads.push({
        uploadItem,
        file,
        subsector: '', // Will be set by caller
        index,
      });

      // Notify progress callback that this file completed successfully
      onFileProgress?.(index, file.name, 'success');
    } catch (error) {
      console.error(`Failed to upload file ${file.name}:`, error);
      onFileProgress?.(index, file.name, 'error');
      // Continue with other files, don't break the loop
    }
  }

  return successfulUploads;
};

export const createSourceWithFilesForExistingSource = async (
  agencyBaseId: string,
  sourceBaseId: string,
  files: File[]
): Promise<{
  sourceId: string;
  fileUploadUrls: Array<UploadUrlItem>;
}> => {
  const filesData = files.map((file) => ({
    name: file.name,
    contentType: file.type || 'application/octet-stream',
  }));

  const response = await apiDev.post(
    '/source/file/get-upload-urls-for-existing-source',
    {
      agencyBaseId,
      sourceBaseId,
      files: filesData,
      expiresIn: 3600,
    }
  );

  return {
    sourceId: sourceBaseId,
    fileUploadUrls: response.data.response.fileUploadUrls,
  };
};

/**
 * Utility function to decode HTML entities in URLs
 */
export const decodeUploadUrl = (url: string): string => {
  return url
    .replace(/&amp;/g, '&')
    .replace(/&#x3D;/g, '=')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"');
};
