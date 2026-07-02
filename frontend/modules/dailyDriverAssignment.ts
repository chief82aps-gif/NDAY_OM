import { DashboardModule } from './types';

export const dailyDriverAssignmentModule: DashboardModule = {
  id: 'daily-driver-assignment',
  title: 'Daily Driver Assignment',
  description: 'Upload DOP, fleet, Cortex, and route sheet data to generate assignments.',
  href: '/upload?view=daily',
};
