import { DashboardModule } from './types';

export const featureFlagsModule: DashboardModule = {
  id: 'feature-flags',
  title: 'Feature Flags',
  description: 'Toggle app features live, no redeploy needed.',
  href: '/feature-flags',
  allowedRoles: ['admin'],
};
