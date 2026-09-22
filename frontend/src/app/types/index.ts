export interface Classroom {
  id: number;
  name: string;
  capacity: number;
  room_type: 'normal' | 'lab' | 'multimedia';
  equipment: string[];
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface Teacher {
  id: number;
  name: string;
  subject: string;
  phone?: string;
  email?: string;
  available_time_slots: { day: number; period: number }[];
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface Class {
  id: number;
  grade: number;
  name: string;
  student_count: number;
  class_teacher?: number;
  class_teacher_name?: string;
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface Course {
  id: number;
  name: string;
  weekly_hours: number;
  preferred_room_type: 'normal' | 'lab' | 'multimedia';
  priority: 'high' | 'medium' | 'low';
  is_active: boolean;
  created_at?: string;
  updated_at?: string;
}

export interface Semester {
  id: number;
  name: string;
  start_date: string;
  end_date: string;
  is_active: boolean;
  daily_periods: { name: string; order: number; start_time?: string; end_time?: string }[];
  weekly_days: number;
  holidays: string[];
  created_at?: string;
  updated_at?: string;
}

export interface ClassCourse {
  id: number;
  class_id: number;
  course: number;
  teacher: number;
  semester: number;
  course_name?: string;
  teacher_name?: string;
  class_name?: string;
  weekly_hours?: number;
}

export interface ScheduleEntry {
  id: number;
  semester: number;
  class_id: number;
  course: number;
  teacher: number;
  classroom: number;
  day_of_week: number;
  period: number;
  is_locked: boolean;
  is_conflict: boolean;
  conflict_type?: string;
  original_teacher?: number;
  original_teacher_name?: string;
  course_name?: string;
  teacher_name?: string;
  classroom_name?: string;
  class_name?: string;
  suspension_records?: SuspensionDisposition[];
  created_at?: string;
  updated_at?: string;
}

export interface SuspensionDisposition {
  id: number;
  suspension: number;
  entry: number | null;
  action: 'relocated' | 'cancelled';
  action_display?: string;
  original_classroom: number;
  original_classroom_name?: string;
  new_classroom?: number | null;
  new_classroom_name?: string | null;
  class_name?: string;
  course_name?: string;
  day_of_week?: number;
  period?: number;
  reason?: string;
  start_date?: string;
  end_date?: string;
  suspension_status?: 'pending' | 'applied' | 'recovered';
  created_at?: string;
}

export type SuspensionStatus = 'pending' | 'applied' | 'recovered';
export type SuspensionDisposal = 'relocate' | 'cancel';

export interface ClassroomSuspension {
  id: number;
  classroom: number;
  classroom_name?: string;
  semester: number;
  semester_name?: string;
  start_date: string;
  end_date: string;
  reason: string;
  status: SuspensionStatus;
  status_display?: string;
  disposal: '' | SuspensionDisposal;
  disposal_display?: string;
  dispositions?: SuspensionDisposition[];
  confirmed_at?: string;
  recovered_at?: string;
  created_at?: string;
  updated_at?: string;
}

export interface SuspensionPreviewItem {
  entry: ScheduleEntry;
  candidates: Pick<Classroom, 'id' | 'name' | 'capacity' | 'room_type'>[];
  chosen_classroom_id: number | null;
  block_reason: string | null;
}

export interface SuspensionPreview {
  suspension: ClassroomSuspension;
  items: SuspensionPreviewItem[];
  blocked: boolean;
  blocking_reasons: string[];
  affected_count: number;
}

export interface SuspensionConfirmResult {
  status: 'applied' | 'recovered' | 'blocked';
  already_applied?: boolean;
  already_recovered?: boolean;
  message?: string;
  suspension?: ClassroomSuspension;
  results?: SuspensionDisposition[];
  blocking_reasons?: string[];
  items?: {
    entry_id: number;
    chosen_classroom_id: number | null;
    block_reason: string | null;
  }[];
}

export interface Conflict {
  id: number;
  semester: number;
  conflict_type: 'teacher' | 'classroom' | 'class';
  day_of_week: number;
  period: number;
  involved_entries: number[];
  message: string;
  resolved: boolean;
  created_at?: string;
}

export interface SwapRequest {
  id: number;
  semester: number;
  requesting_teacher: number;
  target_teacher: number;
  entry1: number;
  entry2: number;
  reason: string;
  status: 'pending' | 'approved' | 'rejected';
  requesting_teacher_name?: string;
  target_teacher_name?: string;
  created_at?: string;
  updated_at?: string;
}

export interface Substitute {
  id: number;
  semester: number;
  original_teacher: number;
  substitute_teacher: number;
  affected_entry: number;
  start_date: string;
  end_date: string;
  reason: string;
  is_active: boolean;
  original_teacher_name?: string;
  substitute_teacher_name?: string;
  created_at?: string;
  updated_at?: string;
}
