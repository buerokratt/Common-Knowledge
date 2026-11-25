import { FC, useEffect } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useCookies } from 'react-cookie';
import { useTranslation } from 'react-i18next';

import { CKBLayout } from 'components';
import useStore from 'store';
import { UserInfo } from 'types/userInfo';
import { redirectToLogin } from 'services/api';

import AgencyList from 'pages/Agency';
import Files from 'pages/Files';
import Agency from 'pages/Agency/Agency';
import Settings from 'pages/Settings';
import Reports from 'pages/Reports';
import Report from 'pages/Reports/Report';
import AddAgency from 'pages/Agency/SaveAgency';

// Import new API components
import ApiList from 'pages/API';
import ApiDetail from 'pages/API/ApiDetail';

import './locale/et_EE';
import useTabCloseEffect from 'hooks/useTabCloseEffects';

const customJwtCookieKey = 'customJwtCookie';

// Mirrors main.tsx: when true, user data comes from the mocked generic/ routes.
const isLocal = import.meta.env.REACT_APP_LOCAL?.toLowerCase() === 'true';

// The axios error may be the raw AxiosError or AxiosInterceptor's wrapped Error;
// both keep the HTTP response on `.response`.
const getErrorStatus = (error: unknown): number | undefined =>
  (error as { response?: { status?: number } } | null)?.response?.status;

const App: FC = () => {
  const { t } = useTranslation();
  const userInfo = useStore((state) => state.userInfo);
  const [_, setCookie] = useCookies([customJwtCookieKey]);

  // JWT expiration check logic moved from Header
  useEffect(() => {
    const interval = setInterval(() => {
      const expirationTimeStamp = localStorage.getItem('exp');
      if (
        expirationTimeStamp !== 'null' &&
        expirationTimeStamp !== null &&
        expirationTimeStamp !== undefined
      ) {
        const expirationDate = new Date(parseInt(expirationTimeStamp) ?? '');
        const currentDate = new Date(Date.now());
        if (expirationDate < currentDate) {
          localStorage.removeItem('exp');
          // Session expired. Login is handled outside this module, so there is
          // nowhere to redirect to - paste a fresh customJwtCookie to continue.
          console.warn('Session expired: set a new customJwtCookie to continue.');
        }
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [userInfo]);

  const { error: userInfoError } = useQuery<{
    data: { custom_jwt_userinfo: UserInfo };
  }>({
    queryKey: [isLocal ? 'userinfo' : 'auth/jwt/userinfo', 'prod'],
    // 401/403 won't change on retry; only retry other (e.g. network) failures.
    retry: (failureCount: number, error: unknown) =>
      ![401, 403].includes(getErrorStatus(error) ?? 0) && failureCount < 3,
    onSuccess: (res: { response: UserInfo }) => {
      // The mock has no real expiry, so skip the expiration bookkeeping locally.
      if (!isLocal) {
        localStorage.setItem('exp', res.response.JWTExpirationTimestamp);
      }
      return useStore.getState().setUserInfo(res.response);
    },
    onError: (error: unknown) => {
      if (getErrorStatus(error) === 401) {
        localStorage.removeItem('exp');
        redirectToLogin();
      }
    },
  });

  useTabCloseEffect();

  // Render nothing of the app (no menu, no page content) until the user check
  // has passed, so a logged-out user never sees content before the redirect.
  if (!userInfo) {
    const status = getErrorStatus(userInfoError);
    if (status === 403) return <p>{t('global.forbidden')}</p>;
    if (userInfoError && status !== 401)
      return <p>{t('global.notificationErrorMsg')}</p>;
    return <p>{t('global.loading')}</p>;
  }

  return (
    <Routes>
      <Route element={<CKBLayout />}>
        <Route index element={<Navigate to="/agency" />} />

        {/* Agency routes */}
        <Route path="/agency" element={<AgencyList />} />
        <Route path="/agency/add" element={<AddAgency />} />
        <Route path="/agency/:id" element={<Agency />} />

        {/* API Integration routes */}
        <Route path="/api" element={<ApiList />} />
        <Route path="/api/:id" element={<ApiDetail />} />
        <Route path="/api/:id/schedule" element={<Settings />} />

        {/* Unified files route */}
        <Route path="/source/:id/files" element={<Files />} />

        {/* Reports routes */}
        <Route path="/reports" element={<Reports />} />
        <Route path="/reports/:id" element={<Report />} />

        {/* Settings route */}
        <Route path="/source/:id/schedule" element={<Settings />} />
      </Route>
    </Routes>
  );
};

export default App;
