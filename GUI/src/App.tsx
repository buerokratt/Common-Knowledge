import { FC } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { CKBLayout } from 'components';
import useStore from 'store';
import { UserInfo } from 'types/userInfo';

import AgencyList from 'pages/Agency';
import Pages from 'pages/Pages';
import Agency from 'pages/Agency/Agency';
import Settings from 'pages/Settings';
import Files from 'pages/Files';
import Reports from 'pages/Reports';
import Report from 'pages/Reports/Report';
import AddAgency from 'pages/Agency/AddAgency';

import './locale/et_EE';

const App: FC = () => {
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
        <Route path="/agency" element={<AgencyList />} />
        <Route path="/agency/add" element={<AddAgency />} />
        <Route path="/agency/:id" element={<Agency />} />
        <Route path="/pages" element={<Pages />} />
        <Route path="/files" element={<Files />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/reports/:id" element={<Report />} />
        <Route path="/settings" element={<Settings />} />
      </Route>
    </Routes>
  );
};

export default App;
