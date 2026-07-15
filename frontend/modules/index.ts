import { assignmentDatabaseModule } from './assignmentDatabase';
import { adminPanelModule } from './adminPanel';
import { attendanceModule } from './attendance';
import { attendanceReportsModule } from './attendanceReports';
import { opsIngestModule } from './opsIngest';
import { dvicModule } from './dvic';
import { dspScorecardModule } from './dspScorecard';
import { eodSurveyModule } from './eodSurvey';
import { driverQualityModule } from './driverQuality';
import { routeAssignmentModule } from './routeAssignment';
import { dailyDriverAssignmentModule } from './dailyDriverAssignment';
import { dailyScreenshotAuditModule } from './dailyScreenshotAudit';
import { driverScheduleModule } from './driverSchedule';
import { financialUploadsModule } from './financialUploads';
import { invoiceAuditModule } from './invoiceAudit';
import { performanceUploadsModule } from './performanceUploads';
import { rescueTrackerModule } from './rescueTracker';
import { adpStatusModule } from './adpStatus';
import { okamiCapacityModule } from './okamiCapacity';
import { disciplineTrackerModule } from './disciplineTracker';
import { driversModule } from './drivers';
import { waveStatusModule } from './waveStatus';
import { DashboardModule } from './types';

export const dashboardModules: DashboardModule[] = [
  routeAssignmentModule,
  dailyDriverAssignmentModule,
  driverScheduleModule,
  rescueTrackerModule,
  invoiceAuditModule,
  financialUploadsModule,
  performanceUploadsModule,
  dailyScreenshotAuditModule,
  assignmentDatabaseModule,
  adminPanelModule,
  attendanceModule,
  attendanceReportsModule,
  opsIngestModule,
  dvicModule,
  dspScorecardModule,
  driverQualityModule,
  eodSurveyModule,
  adpStatusModule,
  okamiCapacityModule,
  waveStatusModule,
  driversModule,
  disciplineTrackerModule,
];
