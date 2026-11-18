import { apiDev } from './api';

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
  type: 'raw' | 'cleaned';
  file?: UploadedFile | ScrapedFile;
  fileId?: string;
  content: string;
  sourceType: 'uploaded' | 'scraped' | 'api';
  loading: boolean;
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

export const bulkRefreshFiles = async (fileIds: string[]): Promise<void> => {
  await apiDev.post('/source-file/refresh-multiple', {
    baseIds: fileIds,
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
 * Update exclusion status of multiple files in bulk
 */
export const bulkUpdateFileExclusion = async (
  fileIds: string[],
  isExcluded: boolean
): Promise<void> => {
  await apiDev.post('/source-file/bulk-exclude', {
    baseIds: fileIds,
    excluded: isExcluded,
  });
};

/**
 * Delete multiple files in bulk
 */
export const bulkDeleteFiles = async (fileIds: string[]): Promise<void> => {
  await apiDev.post('/source-file/bulk-remove', {
    baseIds: fileIds,
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

