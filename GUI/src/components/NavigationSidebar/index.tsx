import React, { FC } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import './NavigationSidebar.scss';

interface MenuItemsProps {
  id: string;
  label: string;
  path: string;
  activeRoutes?: string[];
  icon?: any;
}

interface SidebarProps {
  title?: string;
  menuItems: MenuItemsProps[];
}

const Sidebar: FC<SidebarProps> = ({
  title = 'Common Knowledge Base',
  menuItems,
}) => {
  const location = useLocation();

  const isMenuItemActive = (item: MenuItemsProps): boolean => {
    // If activeRoutes is defined, check if current pathname matches any of them
    if (item.activeRoutes && item.activeRoutes.length > 0) {
      return item.activeRoutes.some((route) =>
        location.pathname.startsWith(route)
      );
    }
    // Fallback to default behavior - exact match or starts with path
    return (
      location.pathname === item.path ||
      location.pathname.startsWith(item.path + '/')
    );
  };

  return (
    <aside className="sidebar">
      <div className="sidebar__header">
        <h1 className="sidebar__title">{title}</h1>
      </div>

      <nav className="sidebar__nav">
        <ul className="sidebar__menu">
          {menuItems.map((item) => (
            <li key={item.id}>
              <NavLink
                to={item.path}
                end={!item.activeRoutes} // Only use end prop when activeRoutes is not provided
                className={({ isActive }) => {
                  // Use custom active logic if activeRoutes is provided
                  const shouldBeActive = item.activeRoutes
                    ? isMenuItemActive(item)
                    : isActive;

                  return `sidebar__menu-item ${
                    shouldBeActive ? 'sidebar__menu-item--active' : ''
                  }`;
                }}
              >
                <div className="sidebar__link">
                  <div className="sidebar__content">
                    <span className="sidebar__icon">{item.icon}</span>
                    <span className="sidebar__label">{item.label}</span>
                  </div>
                </div>
              </NavLink>
            </li>
          ))}
        </ul>
      </nav>
    </aside>
  );
};

export default Sidebar;
