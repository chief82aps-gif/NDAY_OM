import { DashboardModule } from './types';

export const eodSurveyModule: DashboardModule = {
  id: 'eod-survey',
  title: 'End of Day Survey',
  description: 'Driver check-out survey posted daily to #driver-dashboard. Tracks clock-out, van condition, incidents, injuries, RTS packages, and sweeps. Sends a gentle DM reminder to any scheduled driver who hasn\'t submitted by 7:30 PM.',
  href: '/eod-admin',
};
