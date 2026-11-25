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

const notificationApiDev = axios.create({
  baseURL: import.meta.env.REACT_APP_NOTIFICATION_NODE_URL,
  headers: {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
  },
  withCredentials: false,
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
  return Promise.reject(new Error(error.message));
};

const redirectToLogin = () => {
  const loginUrl = import.meta.env.REACT_APP_CUSTOMER_SERVICE_LOGIN;
  if (!loginUrl) {
    console.error('Session expired, but REACT_APP_CUSTOMER_SERVICE_LOGIN is not set.');
    return;
  }
  window.location.href = loginUrl;
};

// Registered at module load, so it runs before AxiosInterceptor's handlers
// (added later in a useEffect) and still sees the raw AxiosError status.
// 401 = no/expired session -> go to login. 403 = logged in without the
// required role -> just reject, so the user stays and the page shows its error.
const handleUnauthorizedResponse = (error: AxiosError) => {
  if (error.response?.status === 401) {
    redirectToLogin();
  }
  return Promise.reject(error);
};

api.interceptors.request.use(
  (axiosRequest) => axiosRequest,
  handleRequestError
);
api.interceptors.response.use(
  (response) => response,
  handleUnauthorizedResponse
);

const BACKOFFICE_ROUTES = [
  'accounts/user-role',
  'accounts/customer-support-activity',
  'accounts/settings',
  'accounts/logout',
  'accounts/admin/',
  'auth/jwt/',
  'agents/chats/',
  'chats/',
  'comments/',
  'configs/bot-config',
];

const isBackofficeRoute = (url = '') =>
  BACKOFFICE_ROUTES.some((route) => url.replace(/^\/+/, '').startsWith(route));

apiDev.interceptors.request.use(
  (axiosRequest) => {
    if (isBackofficeRoute(axiosRequest.url)) {
      axiosRequest.baseURL =
        import.meta.env.REACT_APP_RUUTER_PRIVATE_API_URL ?? axiosRequest.baseURL;
    }
    return axiosRequest;
  },
  handleRequestError
);
apiDev.interceptors.response.use(
  (response) => response,
  handleUnauthorizedResponse
);

export { api, apiDev, notificationApiDev, AxiosInterceptor, redirectToLogin };
