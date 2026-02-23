"""Asana API integration for new driver scheduling and task management."""
import os
import requests
from typing import List, Dict, Optional, Tuple
from datetime import datetime


class AsanaClient:
    """Client for interacting with Asana API."""
    
    def __init__(self, api_token: str = None):
        """Initialize Asana client with API token."""
        self.api_token = api_token or os.getenv('ASANA_API_TOKEN')
        if not self.api_token:
            raise ValueError("ASANA_API_TOKEN environment variable not set")
        
        self.base_url = "https://app.asana.com/api/1.0"
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Accept": "application/json",
        }
    
    def get_project_by_name(self, project_name: str) -> Optional[Dict]:
        """Find Asana project by name."""
        try:
            url = f"{self.base_url}/projects"
            params = {"opt_fields": "name,gid"}
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            
            projects = response.json()["data"]
            for project in projects:
                if project["name"].lower() == project_name.lower():
                    return project
            return None
        except Exception as e:
            raise Exception(f"Failed to get project: {str(e)}")
    
    def get_tasks_in_project(self, project_gid: str, section_name: str = None) -> List[Dict]:
        """Get tasks in a project, optionally filtered by section."""
        try:
            url = f"{self.base_url}/projects/{project_gid}/tasks"
            params = {
                "opt_fields": "gid,name,custom_fields,assignee,completed,due_on",
            }
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            
            tasks = response.json()["data"]
            
            # Filter by section if provided
            if section_name:
                tasks = [t for t in tasks if t.get("section_name") == section_name]
            
            return tasks
        except Exception as e:
            raise Exception(f"Failed to get project tasks: {str(e)}")
    
    def get_task(self, task_gid: str) -> Dict:
        """Get a specific task with all details."""
        try:
            url = f"{self.base_url}/tasks/{task_gid}"
            params = {"opt_fields": "gid,name,custom_fields,assignee,completed,notes,due_on"}
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            return response.json()["data"]
        except Exception as e:
            raise Exception(f"Failed to get task: {str(e)}")
    
    def update_task(self, task_gid: str, updates: Dict) -> Dict:
        """Update a task with new data."""
        try:
            url = f"{self.base_url}/tasks/{task_gid}"
            payload = {"data": updates}
            response = requests.put(url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()["data"]
        except Exception as e:
            raise Exception(f"Failed to update task: {str(e)}")
    
    def add_custom_field(self, task_gid: str, custom_field_gid: str, value: str) -> Dict:
        """Add/update a custom field on a task."""
        try:
            url = f"{self.base_url}/tasks/{task_gid}"
            payload = {
                "data": {
                    "custom_fields": {
                        custom_field_gid: value
                    }
                }
            }
            response = requests.put(url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()["data"]
        except Exception as e:
            raise Exception(f"Failed to add custom field: {str(e)}")
    
    def create_task(self, project_gid: str, task_data: Dict) -> Dict:
        """Create a new task in a project."""
        try:
            url = f"{self.base_url}/tasks"
            task_data["projects"] = [project_gid]
            payload = {"data": task_data}
            response = requests.post(url, headers=self.headers, json=payload)
            response.raise_for_status()
            return response.json()["data"]
        except Exception as e:
            raise Exception(f"Failed to create task: {str(e)}")
    
    def get_custom_field_by_name(self, project_gid: str, field_name: str) -> Optional[Dict]:
        """Get a custom field by name from a project."""
        try:
            url = f"{self.base_url}/projects/{project_gid}/custom_field_settings"
            params = {"opt_fields": "custom_field.name,custom_field.gid,custom_field.type"}
            response = requests.get(url, headers=self.headers, params=params)
            response.raise_for_status()
            
            fields = response.json()["data"]
            for field in fields:
                if field["custom_field"]["name"].lower() == field_name.lower():
                    return field["custom_field"]
            return None
        except Exception as e:
            raise Exception(f"Failed to get custom fields: {str(e)}")


class NewDriverScheduler:
    """Handle scheduling logic for new drivers from Asana."""
    
    def __init__(self, asana_client: AsanaClient):
        self.asana = asana_client
    
    def get_new_drivers_from_asana(
        self,
        project_name: str,
        section_name: str = "Ready to Schedule",
    ) -> List[Dict]:
        """
        Pull new drivers from Asana project/section.
        
        Args:
            project_name: Name of Asana project (e.g., "Hiring")
            section_name: Section name for ready-to-schedule drivers
            
        Returns:
            List of driver task data with name, email, phone, etc.
        """
        errors = []
        drivers = []
        
        try:
            # Get project
            project = self.asana.get_project_by_name(project_name)
            if not project:
                errors.append(f"Asana project '{project_name}' not found")
                return drivers, errors
            
            project_gid = project["gid"]
            
            # Get tasks (new drivers)
            tasks = self.asana.get_tasks_in_project(project_gid, section_name)
            
            for task in tasks:
                if task.get("completed"):
                    continue  # Skip completed tasks
                
                driver_info = {
                    "asana_task_gid": task["gid"],
                    "name": task["name"],
                    "custom_fields": task.get("custom_fields", []),
                    "assignee": task.get("assignee"),
                }
                drivers.append(driver_info)
            
            return drivers, errors
        except Exception as e:
            errors.append(f"Failed to get new drivers from Asana: {str(e)}")
            return drivers, errors
    
    def create_scheduling_suggestion(
        self,
        new_drivers: List[Dict],
        current_schedule: Dict,
        weeks_ahead: int = 1,
    ) -> Dict:
        """
        Create scheduling suggestions for new drivers based on team load balancing.
        
        Args:
            new_drivers: List of new driver info from Asana
            current_schedule: Current driver schedule data
            weeks_ahead: How many weeks to plan ahead
            
        Returns:
            Dictionary with scheduling suggestions
        """
        suggestions = {
            "new_drivers": [],
            "recommendations": [],
        }
        
        # Calculate team load per day/wave
        team_load = self._calculate_team_load(current_schedule)
        
        for driver in new_drivers:
            # Find under-loaded days/waves
            best_assignments = self._find_best_assignments(team_load, count=3)
            
            suggestions["new_drivers"].append({
                "name": driver["name"],
                "asana_task_gid": driver["asana_task_gid"],
                "suggested_assignments": best_assignments,
            })
        
        return suggestions
    
    def _calculate_team_load(self, schedule: Dict) -> Dict:
        """Calculate current team distribution across days/waves."""
        load = {}
        # TODO: Implement based on schedule structure
        return load
    
    def _find_best_assignments(self, team_load: Dict, count: int = 3) -> List[Dict]:
        """Find the most under-loaded day/wave combinations."""
        # TODO: Implement load balancing logic
        return []
    
    def push_assignment_to_asana(
        self,
        task_gid: str,
        assignment: Dict,
        project_gid: str,
    ) -> Tuple[bool, str]:
        """
        Push a schedule assignment back to Asana task.
        
        Args:
            task_gid: Asana task GID
            assignment: Assignment data (date, wave, show_time, etc.)
            project_gid: Project GID for custom field reference
            
        Returns:
            Tuple of (success, message)
        """
        try:
            # Find custom field for assignment
            field = self.asana.get_custom_field_by_name(project_gid, "Schedule Assignment")
            
            assignment_text = (
                f"Assigned: {assignment.get('date')} "
                f"@ {assignment.get('show_time')} "
                f"(Wave: {assignment.get('wave_time')})"
            )
            
            # Update task
            updates = {
                "notes": assignment_text,
                "due_on": assignment.get("date"),
            }
            
            if field:
                self.asana.add_custom_field(task_gid, field["gid"], assignment_text)
            
            self.asana.update_task(task_gid, updates)
            
            return True, "Assignment pushed to Asana successfully"
        except Exception as e:
            return False, f"Failed to push assignment to Asana: {str(e)}"
