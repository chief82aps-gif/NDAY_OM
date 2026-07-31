import { DashboardModule } from './types';

export const adminHomeModule: DashboardModule = {
  id: 'admin-home',
  title: 'Admin Home',
  description: 'Feature flags, glitch reports, pending redemptions, and system tools.',
  href: '/admin-home',
  allowedRoles: ['admin'],
};
