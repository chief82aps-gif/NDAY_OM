import { DashboardModule } from './types';

export const financialUploadsModule: DashboardModule = {
  id: 'financial-data',
  title: 'Financial Uploads',
  description: 'Run WST vs Cortex/DOP daily comparisons and weekly dispute reports; invoice ingest itself lives under Invoice Ingest Tools.',
  href: '/upload?view=financial',
  allowedRoles: ['admin', 'manager'],
};
