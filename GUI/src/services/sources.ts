import { apiDev } from './api';
import {
  createSourceWithFiles,
  createSourceWithFilesForExistingSource,
  registerUploadedFiles,
  uploadFilesToS3WithProgress,
  UploadedFileInfo,
  FileProgressCallback,
} from './s3';

export interface Source {
  id: string;
  baseId: string;
  url: string;
  subsector: string;
  lastScrapedAt: string;
  status: string;
  agencyBaseId: string;
  createdAt: string;
  updatedAt: string;
  cronSchedule?: string;
  updateAutomatically?: boolean;
}

// API Integration interface - extends Source but with specific properties
export interface ApiIntegration {
  id: string;
  baseId: string;
  name?: string;
  url: string;
  lastScrapedAt: string;
  status: string;
  agencyBaseId: string;
  createdAt: string;
  updatedAt: string;
  cronSchedule?: string;
  updateAutomatically?: boolean;
}

export interface ApiResponse {
  response: any;
}

export interface SourcesListResponse {
  data: Source[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface ApiIntegrationsListResponse {
  data: ApiIntegration[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface SourcesListParams {
  agencyBaseId: string;
  page?: number;
  pageSize?: number;
  sorting?: string;
}

export interface ApiIntegrationsListParams {
  page?: number;
  pageSize?: number;
  sorting?: string;
}

export interface CreateSourceFileRequest {
  agencyBaseId: string;
  subsector: string;
  type: 'file';
  files: File[];
}

// New interface for adding files to existing source
export interface AddFilesToExistingSourceRequest {
  agencyBaseId: string;
  sourceBaseId: string;
  subsector: string;
  type: 'file';
  files: File[];
}

export interface CreateSourceRequest {
  agencyBaseId: string;
  url?: string;
  subsector: string;
  type: 'file' | 'url' | 'api';
  files?: File[];
  apiUrl?: string;
}

export interface UpdateSourceSubsectorRequest {
  subsector: string;
}

// API Source File interface - for API integration files
export interface ApiSourceFile {
  id: string;
  baseId: string;
  name: string;
  pageTitle: string;
  url?: string;
  externalId?: string;
  isExcluded: boolean;
  status: string;
  lastScrapedAt: string;
  originalDataUrl?: string;
  cleanedDataUrl?: string;
  editedDataUrl?: string;
  createdAt: string;
  updatedAt: string;
}

export interface ApiSourceFilesListResponse {
  data: ApiSourceFile[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface ApiSourceFilesListParams {
  sourceId?: string;
  page?: number;
  pageSize?: number;
  sorting?: string;
  search?: string;
  type: 'api_file';
}

// Re-export types that might be needed by consumers
export type { FileProgressCallback } from './s3';

/**
 * Get all sources for a specific agency
 */
export const getSources = async (
  params: SourcesListParams
): Promise<SourcesListResponse> => {
  const response = await apiDev.get(`/source/all`, {
    params: {
      agencyBaseId: params.agencyBaseId,
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || 'last_scraped_at desc',
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Transform the API response to match our expected structure
  const sources = apiResponse.response || [];
  const firstItem = sources[0];

  return {
    data: sources,
    total: firstItem?.total,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

/**
 * Get all API integrations
 */
export const getApiIntegrations = async (
  params: ApiIntegrationsListParams
): Promise<ApiIntegrationsListResponse> => {
  const response = await apiDev.get(`/source/api/all`, {
    params: {
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || 'last_scraped_at desc',
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Transform the API response to match our expected structure
  const apiIntegrations = apiResponse.response || [];
  const firstItem = apiIntegrations[0];

  return {
    data: apiIntegrations,
    total: firstItem?.total,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

/**
 * Create a new source with file upload using S3
 */
export const createSourceFile = async (
  data: CreateSourceFileRequest,
  onFileProgress?: FileProgressCallback
): Promise<Source> => {
  if (!data.files || data.files.length === 0) {
    throw new Error('No files provided');
  }

  try {
    // Step 1: Create source and get upload URLs
    const uploadResponse = await createSourceWithFiles(
      data.agencyBaseId,
      data.subsector,
      data.files
    );

    // Step 2: Upload files to S3 with progress tracking
    const successfulUploads = await uploadFilesToS3WithProgress(
      uploadResponse.fileUploadUrls,
      data.files,
      onFileProgress
    );

    // Step 3: Register only successfully uploaded files in database
    if (successfulUploads.length > 0) {
      try {
        const filesToRegister = successfulUploads.map((uploadInfo) => ({
          base_id: uploadInfo.uploadItem.sourceFileId,
          file_name: uploadInfo.uploadItem.fileName,
          subsector: data.subsector,
          original_data_url: uploadInfo.uploadItem.path,
        }));

        await registerUploadedFiles(
          data.agencyBaseId,
          uploadResponse.sourceId,
          filesToRegister
        );
      } catch (registrationError) {
        console.error('Failed to register uploaded files:', registrationError);
        // Don't throw here - files were uploaded successfully to S3
        // You might want to show a warning to the user instead
      }
    }

    // Return a source object
    return {
      id: uploadResponse.sourceId,
      baseId: uploadResponse.sourceId,
      url: 'Files',
      subsector: data.subsector,
      lastScrapedAt: new Date().toISOString(),
      status: 'running',
      agencyBaseId: data.agencyBaseId,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
  } catch (error) {
    console.error('Error creating source with files:', error);
    throw error;
  }
};

/**
 * Add files to an existing source using S3
 */
export const addFilesToExistingSource = async (
  data: AddFilesToExistingSourceRequest,
  onFileProgress?: FileProgressCallback
): Promise<Source> => {
  if (!data.files || data.files.length === 0) {
    throw new Error('No files provided');
  }
  try {
    // Step 1: Get upload URLs for existing source
    const uploadResponse = await createSourceWithFilesForExistingSource(
      data.agencyBaseId,
      data.sourceBaseId,
      data.files
    );

    // Step 2: Upload files to S3 with progress tracking
    const successfulUploads = await uploadFilesToS3WithProgress(
      uploadResponse?.fileUploadUrls,
      data.files,
      onFileProgress
    );

    // Step 3: Register only successfully uploaded files in database
    if (successfulUploads.length > 0) {
      try {
        const filesToRegister = successfulUploads.map((uploadInfo) => ({
          base_id: uploadInfo.uploadItem.sourceFileId,
          file_name: uploadInfo.uploadItem.fileName,
          subsector: data.subsector,
          original_data_url: uploadInfo.uploadItem.path,
        }));

        await registerUploadedFiles(
          data.agencyBaseId,
          data.sourceBaseId,
          filesToRegister
        );
      } catch (registrationError) {
        console.error('Failed to register uploaded files:', registrationError);
        // Don't throw here - files were uploaded successfully to S3
        // You might want to show a warning to the user instead
      }
    }

    // Return a source object (we don't have the full source data, so we construct a minimal one)
    return {
      id: data.sourceBaseId,
      baseId: data.sourceBaseId,
      url: 'Files',
      subsector: data.subsector,
      lastScrapedAt: new Date().toISOString(),
      status: 'running',
      agencyBaseId: data.agencyBaseId,
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
    };
  } catch (error) {
    console.error('Error adding files to existing source:', error);
    throw error;
  }
};

/**
 * Create a new source (URL)
 */
export const createSourceUrl = async (
  data: CreateSourceRequest
): Promise<Source> => {
  const response = await apiDev.post('/source/add', {
    agencyBaseId: data.agencyBaseId,
    url: data.url,
    subsector: data.subsector,
    type: 'url_to_scrape',
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response?.[0] || apiResponse.response;
};

/**
 * Create a new source (API)
 */
export const createSourceApi = async (
  data: CreateSourceRequest
): Promise<Source> => {
  const response = await apiDev.post('/source/add', {
    agencyBaseId: data.agencyBaseId,
    apiUrl: data.apiUrl,
    subsector: data.subsector,
    type: 'api',
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response?.[0] || apiResponse.response;
};

/**
 * Update a source subsector
 */
export const updateSourceSubsector = async (
  sourceId: string,
  data: UpdateSourceSubsectorRequest
): Promise<Source> => {
  const response = await apiDev.post('/source/edit-subsector', {
    baseId: sourceId,
    subsector: data.subsector,
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response?.[0] || apiResponse.response;
};

/**
 * Delete a source
 */
export const deleteSource = async (sourceId: string): Promise<void> => {
  await apiDev.post('/source/remove', {
    baseId: sourceId,
  });
};

/**
 * Stop scraping a source
 */
export const stopSourceScraping = async (sourceId: string): Promise<void> => {
  await apiDev.post('/source/stop', {
    baseId: sourceId,
  });
};

/**
 * Refresh/restart scraping a source
 */
export const refreshSource = async (sourceId: string): Promise<void> => {
  await apiDev.post('/source/refresh', {
    baseId: sourceId,
  });
};

/**
 * Get a specific source by baseId
 */
export const getSource = async (baseId: string): Promise<Source> => {
  const response = await apiDev.get('/source/get', {
    params: {
      baseId: baseId,
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Return the first source from the response array or the response itself
  return apiResponse.response?.[0] || apiResponse.response;
};

/**
 * Get all API source files
 */
export const getApiSourceFiles = async (
  params: ApiSourceFilesListParams
): Promise<ApiSourceFilesListResponse> => {
  const response = await apiDev.get(`/source-file/all`, {
    params: {
      sourceId: params.sourceId,
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || 'last_scraped_at desc',
      search: params.search,
      type: params.type,
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Transform the API response to match our expected structure
  const apiSourceFiles = apiResponse.response || [];
  const firstItem = apiSourceFiles[0];

  return {
    data: apiSourceFiles,
    total: firstItem?.total,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

/**
 * Update source scrape interval
 */
export const updateSourceScrapeInterval = async (
  sourceId: string,
  cronSchedule: string,
  updateAutomatically: boolean
): Promise<Source> => {
  const response = await apiDev.post('/source/edit-scrape-interval', {
    baseId: sourceId,
    cronSchedule: cronSchedule,
    updateAutomatically: updateAutomatically,
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response?.[0] || apiResponse.response;
};
