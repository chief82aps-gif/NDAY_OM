import { assignmentDatabaseModule } from './assignmentDatabase';
import { adminPanelModule } from './adminPanel';
import { dailyDriverAssignmentModule } from './dailyDriverAssignment';
import { dailyScreenshotAuditModule } from './dailyScreenshotAudit';
import { driverScheduleModule } from './driverSchedule';
import { financialUploadsModule } from './financialUploads';
import { invoiceAuditModule } from './invoiceAudit';
import { performanceUploadsModule } from './performanceUploads';
import { rescueTrackerModule } from './rescueTracker';
import { DashboardModule } from './types';

export const dashboardModules: DashboardModule[] = [
  dailyDriverAssignmentModule,
  driverScheduleModule,
  rescueTrackerModule,
  invoiceAuditModule,
  financialUploadsModule,
  performanceUploadsModule,
  dailyScreenshotAuditModule,
  assignmentDatabaseModule,
  adminPanelModule,
];
