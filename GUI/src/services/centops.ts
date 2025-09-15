import { apiDev } from './api';

// Centops interfaces
export interface CentopsClient {
  clientId: string;
  name: string;
  authenticationCertificate: string;
  createdAt: string;
  updatedAt: string | null;
}

export interface CentopsResponse {
  response: {
    items: CentopsClient[];
    page: number;
    pageSize: number;
    totalPages: number;
  };
}

export interface CentopsOption {
  label: string;
  value: string;
}

// Get all Centops clients with pagination
export const getCentopsClients = async (
  page: number = 1,
  pageSize: number = 1000
): Promise<CentopsResponse> => {
  const centopsUrl = import.meta.env.REACT_APP_CENTOPS_API_URL;
  const centopsApiKey = import.meta.env.REACT_APP_CENTOPS_API_KEY;
  const centopsApiSecret = import.meta.env.REACT_APP_CENTOPS_API_SECRET;

  const getAuthorizationHeader = () => {
    const token = `${centopsApiKey}:${centopsApiSecret}`;
    const base64 = btoa(token);
    return base64;
  };

  const response = await apiDev.get(
    `${centopsUrl}/centops/integration/clients`,
    {
      params: {
        page,
        pageSize,
      },
      headers: {
        Authorization: getAuthorizationHeader(),
      },
    }
  );

  return response.data;
};

// Get all Centops clients recursively to handle pagination
export const getAllCentopsClients = async (): Promise<CentopsClient[]> => {
  const allClients: CentopsClient[] = [];
  let currentPage = 1;
  let totalPages = 1;

  try {
    do {
      const response = await getCentopsClients(currentPage, 1000);

      if (response.response && response.response.items) {
        allClients.push(...response.response.items);
        totalPages = response.response.totalPages;
        currentPage++;
      } else {
        break;
      }
    } while (currentPage <= totalPages);

    return allClients;
  } catch (error) {
    console.error('Error fetching Centops clients:', error);
    throw new Error('Failed to fetch Centops clients');
  }
};

// Convert Centops clients to dropdown options
export const getCentopsOptions = async (): Promise<CentopsOption[]> => {
  try {
    const clients = await getAllCentopsClients();

    return clients
      .map((client) => ({
        label: client.name,
        value: client.clientId,
      }))
      .sort((a, b) => a.label.localeCompare(b.label)); // Sort alphabetically by name
  } catch (error) {
    console.error('Error converting Centops clients to options:', error);
    throw error;
  }
};
