import React, { FC } from 'react';
import useStore from 'store';
import { Outlet } from 'react-router-dom';
import { NavigationSidebar, Header } from 'components';
import { useToast } from '../../hooks/useToast';
import './Layout.scss';
import { MdOutlineStorage, MdOutlineAssessment, MdApi } from 'react-icons/md';
import { useTranslation } from 'react-i18next';

const Layout: FC = () => {
  const { t } = useTranslation();
  const menuItems = [
    {
      id: 'data',
      label: t('menu.data'),
      path: '/agency',
      activeRoutes: ['/agency', '/source'],
      icon: <MdOutlineStorage />,
    },
    {
      id: 'reports',
      label: t('menu.reports'),
      path: '/reports',
      icon: <MdOutlineAssessment />,
    },
    {
      id: 'api',
      label: t('menu.apiIntegrations'),
      path: '/api',
      activeRoutes: ['/api'],
      icon: <MdApi />,
    },
  ];
  return (
    <div className="layout">
      <NavigationSidebar menuItems={menuItems} />
      <div className="layout__wrapper">
        <Header toastContext={useToast()} user={useStore.getState().userInfo} />
        <main className="layout__main">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default Layout;
