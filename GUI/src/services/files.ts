import { apiDev } from './api';
import { getUploadUrls, uploadContentToS3 } from './s3';

export interface ScrapedFile {
  id: string;
  baseId: string;
  fileName: string;
  url: string;
  pageTitle: string;
  isExcluded: boolean;
  status: 'scraping' | 'cleaning' | 'finished' | 'not_found' | 'failed';
  originallyScraped: string;
  lastScrapedAt: string;
  sourceId: string;
  createdAt: string;
  updatedAt: string;
  originalDataUrl?: string;
  cleanedDataUrl?: string;
  editedDataUrl?: string;
}

export interface UploadedFile {
  id: string;
  baseId: string;
  fileName: string;
  subsector?: string;
  url?: string;
  isExcluded: boolean;
  status: 'cleaning' | 'finished' | 'failed';
  lastScrapedAt: string;
  sourceId: string;
  createdAt: string;
  updatedAt: string;
  originalDataUrl?: string;
  cleanedDataUrl?: string;
  editedDataUrl?: string;
}

export interface ApiResponse {
  response: any;
}

export interface ScrapedFilesListResponse {
  data: ScrapedFile[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface UploadedFilesListResponse {
  data: UploadedFile[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface ScrapedFilesListParams {
  sourceId?: string;
  isExcluded?: boolean;
  page?: number;
  pageSize?: number;
  sorting?: string;
  search?: string;
}

export interface UploadedFilesListParams {
  sourceId?: string;
  isExcluded?: boolean;
  page?: number;
  pageSize?: number;
  sorting?: string;
  search?: string;
}

export interface EditorState {
  type: 'raw' | 'cleaned' | 'edited';
  file?: UploadedFile | ScrapedFile;
  fileId?: string;
  content: string;
  sourceType: 'uploaded' | 'scraped' | 'api';
  loading: boolean;
  saving?: boolean;
}

/**
 * Get all scraped files with optional filtering and search
 */
export const getScrapedFiles = async (
  params: ScrapedFilesListParams = {}
): Promise<ScrapedFilesListResponse> => {
  const response = await apiDev.get(`/source-file/all`, {
    params: {
      sourceId: params.sourceId,
      type: 'scraped_file',
      isExcluded: params.isExcluded,
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || '',
      search: params.search,
    },
  });

  const apiResponse: ApiResponse = response.data;
  const files = apiResponse.response || [];
  const firstItem = files[0];

  return {
    data: files,
    total: firstItem?.total || files.length,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

/**
 * Get all uploaded files with optional filtering and search
 */
export const getUploadedFiles = async (
  params: UploadedFilesListParams = {}
): Promise<UploadedFilesListResponse> => {
  const response = await apiDev.get(`/source-file/all`, {
    params: {
      sourceId: params.sourceId,
      type: 'uploaded_file',
      isExcluded: params.isExcluded,
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || '',
      search: params.search,
    },
  });

  const apiResponse: ApiResponse = response.data;
  const files = apiResponse.response || [];
  const firstItem = files[0];

  return {
    data: files,
    total: firstItem?.total || files.length,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

/**
 * Update exclusion status of a file
 */
export const updateFileExclusion = async (
  fileId: string,
  isExcluded: boolean
): Promise<ScrapedFile | UploadedFile> => {
  const response = await apiDev.post('/source-file/exclude', {
    baseId: fileId,
    excluded: isExcluded,
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response?.[0] || apiResponse.response;
};

/**
 * Refresh/re-scrape a specific file
 */
export const refreshScrapedFile = async (fileId: string): Promise<void> => {
  await apiDev.post('/source-file/refresh', {
    baseId: fileId,
  });
};

/**
 * Delete a file
 */
export const deleteFile = async (fileId: string): Promise<void> => {
  await apiDev.post('/source-file/remove', {
    baseId: fileId,
  });
};

/**
 * Get download URL for a file
 */
export const getFileDownloadUrl = async (
  path: string
): Promise<{ downloadUrl: string; expiresAt: string }> => {
  const response = await apiDev.get('/get-download-url', {
    params: { path },
  });
  return response.data.response;
};

/**
 * Fetch file content from a path
 */
export const fetchFileData = async (path: string): Promise<string> => {
  const { downloadUrl } = await getFileDownloadUrl(path);
  const response = await fetch(downloadUrl);
  return await response.text();
};

/**
 * Download a file to user's device
 */
export const downloadFile = async (
  path: string,
  fileName: string
): Promise<void> => {
  const { downloadUrl } = await getFileDownloadUrl(path);

  // Create a temporary anchor element and trigger download
  const link = document.createElement('a');
  link.href = downloadUrl;
  link.download = fileName;
  link.target = '_blank'; // Open in new tab as fallback

  // Append to body, click, and remove
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
};

/**
 * Get cleaned content of a file
 */
export const getFileCleanedContent = async (
  fileId: string
): Promise<string> => {
  const response = await apiDev.get('/source-file/content/cleaned', {
    params: { fileId },
  });
  return response.data.response;
};

/**
 * Get edited content of a file
 */
export const getFileEditedContent = async (fileId: string): Promise<string> => {
  const response = await apiDev.get('/source-file/content/edited', {
    params: { fileId },
  });
  return response.data.response;
};

/**
 * Legacy method: Update edited content via API (kept for backward compatibility)
 */
export const updateFileEditedContent = async (
  fileId: string,
  content: string
): Promise<void> => {
  await apiDev.post('/source-file/content/update-edited', {
    fileId: fileId,
    content: content,
  });
};

export const updateFileEditedDataUrl = async (
  baseId: string,
  sourcePath: string
): Promise<void> => {
  const response = await apiDev.post('/source-file/edit-file', {
    base_id: baseId,
    source_file_path: sourcePath,
  });

  const apiResponse = response.data;

  // Check if update was successful
  if (response.status >= 400 || apiResponse.error) {
    throw new Error(
      apiResponse.error?.message || 'Failed to update edited data URL'
    );
  }
};

/**
 * Update edited content using S3 direct upload
 */
export const updateFileEditedContentWithUpload = async (
  file: ScrapedFile | UploadedFile,
  content: string,
  sourcePath: string
): Promise<void> => {
  try {
    // Get upload URL for the target path - Updated to match backend format
    const uploadResponse = await getUploadUrls({
      files: [
        {
          path: `uploads/scrapped-data/${sourcePath}/edited.txt`,
          content_type: 'text/plain',
        },
      ],
      expires_in: 3600,
    });

    if (
      !uploadResponse.upload_urls ||
      uploadResponse.upload_urls.length === 0
    ) {
      throw new Error('No upload URL received');
    }

    const uploadUrl = uploadResponse.upload_urls[0].upload_url;

    // Upload content directly to S3
    await uploadContentToS3(uploadUrl, content, 'text/plain');

    // Update database with the edited data URL
    await updateFileEditedDataUrl(file.baseId, sourcePath);
  } catch (error) {
    console.error('Error updating file with upload:', error);
    throw error;
  }
};
