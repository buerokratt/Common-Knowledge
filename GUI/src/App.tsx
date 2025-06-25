import { FC } from 'react';
import { Navigate, Route, Routes } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';

import { Layout } from 'components';
import useStore from 'store';
import { UserInfo } from 'types/userInfo';

import AgencyList from 'pages/Agency';
import APIList from 'pages/UrlList';
import Settings from 'pages/Settings';
import URL from 'pages/URL';
import FileUpload from 'pages/FileUpload';
import API from 'pages/API';

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
      <Route element={<Layout />}>
        <Route index element={<Navigate to="/agency" />} />
        <Route path="/urls" element={<APIList />} />
        <Route path="/agency" element={<AgencyList />} />
        <Route path="/settings" element={<Settings />} />
        <Route path="/url" element={<URL />} />
        <Route path="/file" element={<FileUpload />} />
        <Route path="/api" element={<API />} />
      </Route>
    </Routes>
  );
};

export default App;
