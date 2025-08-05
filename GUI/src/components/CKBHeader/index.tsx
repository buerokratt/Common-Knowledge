import { FC } from 'react';
import { useTranslation } from 'react-i18next';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from 'components';
import { useToast } from 'hooks/useToast';
import { ReactComponent as BykLogo } from 'assets/logo.svg';
import { apiDev } from 'services/api';
import { AxiosError } from 'axios';
import './Header.scss';

const Header: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();

  const logoutMutation = useMutation({
    mutationFn: () => apiDev.get('accounts/logout'),
    onSuccess(_: any) {
      window.location.href = import.meta.env.REACT_APP_CUSTOMER_SERVICE_LOGIN;
    },
    onError: async (error: AxiosError) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message,
      });
    },
  });

  const handleLogout = () => {
    // Clear any stored tokens/session data
    localStorage.clear();
    sessionStorage.clear();

    // Redirect to login page
    window.location.href = import.meta.env.REACT_APP_LOGIN_URL || '/login';
  };

  return (
    <header className="header">
      <div className="header__container">
        <BykLogo height={50} />
        <Button
          appearance="text"
          onClick={() => {
            localStorage.removeItem('exp');
            logoutMutation.mutate();
          }}
          className="header__logout"
        >
          {t('global.logout')}
        </Button>
      </div>
    </header>
  );
};

export default Header;
