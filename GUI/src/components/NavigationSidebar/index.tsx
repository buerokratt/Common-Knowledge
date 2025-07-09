import React, { FC } from 'react';
import { NavLink } from 'react-router-dom';
import './NavigationSidebar.scss';

interface MenuItemsProps {
  id: string;
  label: string;
  path: string;
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
                className={({ isActive }) =>
                  `sidebar__menu-item ${
                    isActive ? 'sidebar__menu-item--active' : ''
                  }`
                }
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
