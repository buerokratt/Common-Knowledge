import { FC, useEffect } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { useCookies } from 'react-cookie';

import { CKBLayout } from 'components';
import useStore from 'store';
import { UserInfo } from 'types/userInfo';

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

const customJwtCookieKey = 'customJwtCookie';

const App: FC = () => {
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
          window.location.href =
            import.meta.env.REACT_APP_CUSTOMER_SERVICE_LOGIN;
        }
      }
    }, 2000);
    return () => clearInterval(interval);
  }, [userInfo]);

  useQuery<{
    data: { custom_jwt_userinfo: UserInfo };
  }>({
    queryKey: ['auth/jwt/userinfo', 'prod'],
    onSuccess: (res: { response: UserInfo }) => {
      localStorage.setItem('exp', res.response.JWTExpirationTimestamp);
      return useStore.getState().setUserInfo(res.response);
    },
  });

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
