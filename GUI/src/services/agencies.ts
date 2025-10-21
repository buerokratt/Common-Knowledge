import { apiDev } from './api';

// Base Agency interface
export interface Agency {
  id: string;
  baseId: string;
  name: string;
  sector: string;
  updatedAt: string;
  page: string;
  totalPages: number;
  externalId?: string; // Added for edit functionality
}

export interface ApiResponse {
  response: any;
}

export interface AgencyListResponse {
  data: Agency[];
  total: number;
  page: number;
  pageSize: number;
  totalPages: number;
}

export interface AgencyListParams {
  page?: number;
  pageSize?: number;
  sorting?: string;
}

// Create Agency interfaces
export interface CreateAgencyRequest {
  name: string;
  sector: string;
  externalId: string;
}

export interface CreateAgencyResponse {
  id: string;
  baseId: string;
  name: string;
  sector: string;
  externalId: string;
  createdAt: string;
  updatedAt: string;
}

// Update Agency interface
export interface UpdateAgencyRequest {
  name: string;
  sector: string;
  externalId: string;
}

// Get all agencies
export const getAgencies = async (
  params: AgencyListParams = {}
): Promise<AgencyListResponse> => {
  const response = await apiDev.get('/agency/all', {
    params: {
      page: params.page || 1,
      pageSize: params.pageSize || 10,
      sorting: params.sorting || '',
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Transform the API response to match our expected structure
  const agencies = apiResponse.response || [];
  const firstItem = agencies[0];

  return {
    data: agencies,
    total: firstItem?.total,
    page: parseInt(firstItem?.page || '1'),
    pageSize: params.pageSize || 10,
    totalPages: firstItem?.totalPages || 1,
  };
};

// Get a specific agency by baseId
export const getAgency = async (baseId: string): Promise<Agency> => {
  const response = await apiDev.get('/agency/get', {
    params: {
      baseId: baseId,
    },
  });

  const apiResponse: ApiResponse = response.data;

  // Return the first agency from the response array or the response itself
  return apiResponse.response?.[0] || apiResponse.response;
};

// Create a new agency
export const createAgency = async (
  params: CreateAgencyRequest
): Promise<CreateAgencyResponse> => {
  const response = await apiDev.post('/agency/add', {
    name: params.name,
    sector: params.sector,
    externalId: params.externalId,
  });
  const apiResponse: ApiResponse = response.data;

  return apiResponse.response?.[0];
};

// Update an agency
export const updateAgency = async (
  baseId: string,
  data: UpdateAgencyRequest
): Promise<Agency> => {
  const response = await apiDev.post('/agency/edit', {
    baseId: baseId,
    name: data.name,
    sector: data.sector,
    externalId: data.externalId,
  });

  const apiResponse: ApiResponse = response.data;
  return apiResponse.response?.[0] || apiResponse.response;
};

// Delete an agency using baseId
export const deleteAgency = async (baseId: string): Promise<void> => {
  await apiDev.post('/agency/remove', {
    baseId: baseId,
  });
};
