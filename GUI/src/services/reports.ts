import { apiDev } from './api';

// Base Report interface
export interface Report {
  id: string;
  baseId: string;
  agencyBaseId: string;
  agencyName: string;
  url: string;
  errors: number | null;
  scrapingStartedAt: string;
  scrapingFinishedAt: string;
  page: string;
  totalPages: number;
}

export interface ApiResponse {
  response: any;
}

export interface ReportListResponse {
  data: Report[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface ReportListParams {
  page?: number;
  pageSize?: number;
  sorting?: string;
}

// Get all reports
export const getReports = async (
  params: ReportListParams = {}
): Promise<ReportListResponse> => {
  const response = await apiDev.get('/reports/all', {
    params: {
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || '',
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Transform the API response to match our expected structure
  const reports = apiResponse.response || [];
  const firstItem = reports[0];

  return {
    data: reports,
    total: firstItem?.total || 0,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

// Get a specific report by baseId
export const getReport = async (baseId: string): Promise<Report> => {
  const response = await apiDev.get('/reports/logs', {
    params: {
      base_id: baseId,
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Return the first report from the response array or the response itself
  return apiResponse.response?.[0] || apiResponse.response;
};

// Report Page interface
export interface ReportPage {
  id: string;
  baseId: string;
  sourceRunReportBaseId: string;
  url: string;
  errorType: string | null;
  errorMessage: string | null;
  scrapedAt: string;
  page: string;
  totalPages: number;
}

export interface ReportPagesListResponse {
  data: ReportPage[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface ReportPagesListParams {
  source_run_report_base_id: string;
  page?: number;
  pageSize?: number;
  sorting?: string;
}

// Download URL interface
export interface DownloadUrlResponse {
  downloadUrl: string;
  expiresAt: string;
}

// Get report pages
export const getReportPages = async (
  params: ReportPagesListParams
): Promise<ReportPagesListResponse> => {
  const response = await apiDev.get('/reports/logs/all', {
    params: {
      source_run_report_base_id: params.source_run_report_base_id,
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || '',
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Transform the API response to match our expected structure
  const pages = apiResponse.response || [];
  const firstItem = pages[0];

  return {
    data: pages,
    total: firstItem?.total || 0,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

// Get download URL for log files
export const getDownloadUrl = async (
  filePath: string
): Promise<DownloadUrlResponse> => {
  const response = await apiDev.get('/get-download-url', {
    params: {
      path: filePath,
    },
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response;
};

// Delete a report using baseId
export const deleteReport = async (baseId: string): Promise<void> => {
  await apiDev.post('/reports/remove', {
    baseId: baseId,
  });
};
