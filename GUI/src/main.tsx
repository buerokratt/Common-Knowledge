import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';
import {
  QueryClient,
  QueryClientProvider,
  QueryFunction,
} from '@tanstack/react-query';

import App from './App';
import { api, apiDev, AxiosInterceptor } from 'services/api';
import apigeneric from 'services/apigeneric';
import { ToastProvider } from 'context/ToastContext';
import 'styles/main.scss';
import '../i18n';
import { CookiesProvider } from 'react-cookie';

// Local development can opt out of the real TIM-backed auth via REACT_APP_LOCAL=true,
// which routes every 'prod' query at the mocked Ruuter routes under generic/. This is
// the only place that can redirect calls made from inside @buerokratt-ria packages,
// whose query keys are fixed. Never enable outside local development.
const isLocal = import.meta.env.REACT_APP_LOCAL?.toLowerCase() === 'true';

const defaultQueryFn: QueryFunction | undefined = async ({ queryKey }) => {
  if (isLocal && queryKey.includes('prod')) {
    const { data } = await apigeneric.get(queryKey[0] as string);
    // The generic/ mocks wrap their payload twice, so unwrap one level here to
    // match the shape consumers expect from the real routes.
    return data.response;
  }

  if (queryKey.includes('prod')) {
    const { data } = await apiDev.get(queryKey[0] as string);
    return data;
  }

  const { data } = await api.get(queryKey[0] as string);
  return data;
};

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      queryFn: defaultQueryFn,
    },
  },
});

ReactDOM.createRoot(document.getElementById('root') as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter basename={import.meta.env.BASE_URL}>
        <AxiosInterceptor>
          <ToastProvider>
            <CookiesProvider>
              <App />
            </CookiesProvider>
          </ToastProvider>
        </AxiosInterceptor>
      </BrowserRouter>
    </QueryClientProvider>
  </React.StrictMode>
);
