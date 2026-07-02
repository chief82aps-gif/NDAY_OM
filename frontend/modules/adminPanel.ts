import { DashboardModule } from './types';

export const adminPanelModule: DashboardModule = {
  id: 'admin-panel',
  title: 'Admin Panel',
  description: 'Manage users, credentials, and administrative controls.',
  href: '/admin',
  allowedRoles: ['admin'],
};
