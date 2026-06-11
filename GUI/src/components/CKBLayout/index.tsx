import React, { FC } from 'react';
import useStore from 'store';
import { Outlet } from 'react-router-dom';
import { NavigationSidebar} from 'components';
import { useToast } from '../../hooks/useToast';
import './Layout.scss';
import { MdOutlineStorage, MdOutlineAssessment, MdApi } from 'react-icons/md';
import { useTranslation } from 'react-i18next';
import { MainNavigation } from '@buerokratt-ria/menu';
import { Header, useMenuCountConf } from '@buerokratt-ria/header';

const Layout: FC = () => {
  const { t } = useTranslation();
  const domainBarShowing = import.meta.env.REACT_APP_ENABLE_MULTI_DOMAIN?.toLowerCase() === 'true';

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

    const menuCountConf = useMenuCountConf();

  return (
    console.log('Menu count configuration:', menuCountConf), // Debug log to check the menu count configuration --- IGNORE ---
    console.log('Domain selector visibility:', domainBarShowing), // Debug log to check if the domain selector is visible --- IGNORE ---
    <div className="layout">
      {/* <NavigationSidebar menuItems={menuItems} /> */}
      <MainNavigation countConf={menuCountConf} />
      <div className="layout__wrapper">
        <Header 
        toastContext={useToast()} 
        user={useStore.getState().userInfo} 
        setUserDomains={useStore.getState().setUserDomains} 
        isDomainSelectorVisible={domainBarShowing}
/>
        <main className="layout__main">
          <Outlet />
        </main>
      </div>
    </div>
  );
};

export default Layout;
