import { FC } from 'react';
import { useTranslation } from 'react-i18next';
import { Button } from 'components';
import { ReactComponent as BykLogo } from 'assets/logo.svg';
import './Header.scss';

const Header: FC = () => {
  const { t } = useTranslation();

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
          onClick={handleLogout}
          className="header__logout"
        >
          {t('global.logout')}
        </Button>
      </div>
    </header>
  );
};

export default Header;
