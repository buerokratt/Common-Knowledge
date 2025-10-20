import axios, { AxiosError } from 'axios';
import { useTranslation } from 'react-i18next';
import { useEffect } from 'react';

const api = axios.create({
  baseURL: import.meta.env.BASE_URL,
  headers: {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
  },
  withCredentials: true,
});

const apiDev = axios.create({
  baseURL: import.meta.env.REACT_APP_RUUTER_API_URL,
  headers: {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
  },
  withCredentials: true,
});

const AxiosInterceptor = ({ children }) => {
  const { t } = useTranslation();

  useEffect(() => {
    const resInterceptor = (response: any) => {
      import.meta.env.DEBUG_ENABLED && console.debug(response);

      return response;
    };

    const errInterceptor = (error: any) => {
      import.meta.env.DEBUG_ENABLED && console.debug(error);

      // Keep the original error structure for proper error handling
      // If there's a response, attach it to a new error with proper message
      if (error?.response?.data?.response) {
        const responseData = error.response.data.response;
        const errorMessage = typeof responseData === 'string'
          ? responseData
          : responseData.error || t('global.notificationErrorMsg');

        const newError = new Error(errorMessage);
        // Preserve the original response for error handlers
        (newError as any).response = error.response;
        return Promise.reject(newError);
      }

      return Promise.reject(new Error(error?.message || t('global.notificationErrorMsg')));
    };

    const apiInterceptor = api.interceptors.response.use(
      resInterceptor,
      errInterceptor
    );
    const apiDevInterceptor = apiDev.interceptors.response.use(
      resInterceptor,
      errInterceptor
    );

    return () => {
      api.interceptors.response.eject(apiInterceptor);
      apiDev.interceptors.response.eject(apiDevInterceptor);
    };
  }, [t]);

  return children;
};

const handleRequestError = (error: AxiosError) => {
  import.meta.env.DEBUG_ENABLED && console.debug(error);
  if (error.response?.status === 401) {
    // To be added: handle unauthorized requests
  }
  if (error.response?.status === 403) {
    // To be added: handle forbidden requests
  }
  return Promise.reject(new Error(error.message));
};

api.interceptors.request.use(
  (axiosRequest) => axiosRequest,
  handleRequestError
);

apiDev.interceptors.request.use(
  (axiosRequest) => axiosRequest,
  handleRequestError
);

export { api, apiDev, AxiosInterceptor };
