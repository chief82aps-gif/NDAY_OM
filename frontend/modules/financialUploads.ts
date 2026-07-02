import { DashboardModule } from './types';

export const financialUploadsModule: DashboardModule = {
  id: 'financial-data',
  title: 'Financial Uploads',
  description: 'Upload variable invoices, fleet invoices, and weekly incentive files.',
  href: '/upload?view=financial',
  allowedRoles: ['admin', 'manager'],
};
