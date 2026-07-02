export interface DashboardModule {
  id: string;
  title: string;
  description: string;
  href: string;
  allowedRoles?: string[];
}
