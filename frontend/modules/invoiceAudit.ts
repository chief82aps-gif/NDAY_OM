import { DashboardModule } from './types';

export const invoiceAuditModule: DashboardModule = {
  id: 'invoice-audit',
  title: 'Invoice Audit',
  description: 'Review variable invoice totals against uploaded operations data.',
  href: '/audit',
};
