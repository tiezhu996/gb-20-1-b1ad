import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule, ReactiveFormsModule, FormBuilder, FormGroup, Validators } from '@angular/forms';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatFormFieldModule } from '@angular/material/form-field';
import { MatSelectModule } from '@angular/material/select';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatCardModule } from '@angular/material/card';
import { ApiService } from '../../services/api.service';
import type { Classroom, Semester, ClassroomSuspension } from '../../types';

@Component({
  selector: 'app-classroom-suspensions',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    ReactiveFormsModule,
    MatTableModule,
    MatButtonModule,
    MatInputModule,
    MatFormFieldModule,
    MatSelectModule,
    MatIconModule,
    MatChipsModule,
    MatCardModule
  ],
  template: `
    <div class="page-container">
      <h1 class="page-title">教室临时停用</h1>

      <div class="action-bar">
        <button mat-raised-button color="primary" (click)="startCreate()">
          <mat-icon>event_busy</mat-icon>
          登记教室停用
        </button>
        <button mat-button (click)="loadData()">
          <mat-icon>refresh</mat-icon>
          刷新
        </button>
      </div>

      <div *ngIf="showForm" class="form-container">
        <mat-card>
          <mat-card-content>
            <h3>登记教室临时停用</h3>
            <form [formGroup]="form" (ngSubmit)="save()">
              <mat-form-field class="full-width-field">
                <mat-label>教室</mat-label>
                <mat-select formControlName="classroom" required>
                  <mat-option *ngFor="let c of classrooms" [value]="c.id">
                    {{ c.name }}（{{ getRoomTypeLabel(c.room_type) }}，容量{{ c.capacity }}）
                  </mat-option>
                </mat-select>
              </mat-form-field>

              <mat-form-field class="full-width-field">
                <mat-label>学期</mat-label>
                <mat-select formControlName="semester" required>
                  <mat-option *ngFor="let s of semesters" [value]="s.id">
                    {{ s.name }}<span *ngIf="s.is_active">（当前）</span>
                  </mat-option>
                </mat-select>
              </mat-form-field>

              <mat-form-field class="full-width-field">
                <mat-label>停用开始日期</mat-label>
                <input matInput type="date" formControlName="start_date" required>
              </mat-form-field>

              <mat-form-field class="full-width-field">
                <mat-label>停用结束日期</mat-label>
                <input matInput type="date" formControlName="end_date" required>
              </mat-form-field>

              <mat-form-field class="full-width-field">
                <mat-label>停用原因</mat-label>
                <textarea matInput formControlName="reason" rows="2"
                          placeholder="如：电路检修、装修施工、设备维护" required></textarea>
              </mat-form-field>

              <div style="margin-top: 12px;">
                <button mat-raised-button color="primary" type="submit" [disabled]="submitting">
                  登记并匹配替代教室
                </button>
                <button mat-button type="button" (click)="cancel()">取消</button>
              </div>
            </form>
          </mat-card-content>
        </mat-card>
      </div>

      <div *ngIf="message" style="margin-bottom: 16px;">
        <mat-card>
          <mat-card-content>
            <p style="margin: 0;">{{ message }}</p>
          </mat-card-content>
        </mat-card>
      </div>

      <div class="table-container">
        <table mat-table [dataSource]="dataSource" class="mat-elevation-z8">
          <ng-container matColumnDef="classroom">
            <th mat-header-cell *matHeaderCellDef>教室</th>
            <td mat-cell *matCellDef="let item">{{ item.classroom_name }}</td>
          </ng-container>

          <ng-container matColumnDef="semester">
            <th mat-header-cell *matHeaderCellDef>学期</th>
            <td mat-cell *matCellDef="let item">{{ item.semester_name }}</td>
          </ng-container>

          <ng-container matColumnDef="date_range">
            <th mat-header-cell *matHeaderCellDef>停用区间</th>
            <td mat-cell *matCellDef="let item">{{ item.start_date }} 至 {{ item.end_date }}</td>
          </ng-container>

          <ng-container matColumnDef="reason">
            <th mat-header-cell *matHeaderCellDef>原因</th>
            <td mat-cell *matCellDef="let item">{{ item.reason }}</td>
          </ng-container>

          <ng-container matColumnDef="status">
            <th mat-header-cell *matHeaderCellDef>状态</th>
            <td mat-cell *matCellDef="let item">
              <mat-chip [color]="getStatusColor(item.status)" selected>
                {{ item.status_display }}
              </mat-chip>
              <div *ngIf="item.status === 'blocked' && item.blocking_reason"
                   style="font-size: 12px; color: #f44336; margin-top: 4px; max-width: 260px;">
                {{ item.blocking_reason }}
              </div>
            </td>
          </ng-container>

          <ng-container matColumnDef="actions">
            <th mat-header-cell *matHeaderCellDef>操作</th>
            <td mat-cell *matCellDef="let item" class="action-cell">
              <button mat-button (click)="toggleDetail(item)">
                <mat-icon>list</mat-icon>
                {{ selected?.id === item.id ? '收起' : '受影响课程' }}
              </button>
              <button
                mat-raised-button
                color="primary"
                *ngIf="item.status === 'draft' || item.status === 'blocked'"
                [disabled]="confirmingId === item.id"
                (click)="confirm(item)"
              >
                {{ confirmingId === item.id ? '确认中...' : '确认生效' }}
              </button>
              <button
                mat-raised-button
                color="accent"
                *ngIf="item.status !== 'restored'"
                [disabled]="restoringId === item.id"
                (click)="restore(item)"
              >
                {{ restoringId === item.id ? '恢复中...' : '恢复教室' }}
              </button>
            </td>
          </ng-container>

          <tr mat-header-row *matHeaderRowDef="displayedColumns"></tr>
          <tr mat-row *matRowDef="let row; columns: displayedColumns;"></tr>
        </table>

        <div *ngIf="dataSource.length === 0" style="padding: 40px; text-align: center;">
          <p>暂无教室停用记录。</p>
        </div>
      </div>

      <div *ngIf="selected" style="margin-top: 20px;">
        <mat-card>
          <mat-card-content>
            <h3>
              受影响课程与处置明细 —— {{ selected.classroom_name }}
              （{{ selected.start_date }} 至 {{ selected.end_date }}，{{ selected.reason }}）
            </h3>
            <p *ngIf="selected.status === 'blocked'" style="color: #f44336;">
              阻断原因：{{ selected.blocking_reason }}（整批保持原课表）
            </p>
            <table mat-table [dataSource]="selected.items || []" class="mat-elevation-z2">
              <ng-container matColumnDef="course">
                <th mat-header-cell *matHeaderCellDef>课程</th>
                <td mat-cell *matCellDef="let item">
                  {{ item.course_name }}
                  <mat-chip *ngIf="item.is_locked" color="accent" selected>锁定</mat-chip>
                </td>
              </ng-container>

              <ng-container matColumnDef="class">
                <th mat-header-cell *matHeaderCellDef>班级</th>
                <td mat-cell *matCellDef="let item">{{ item.class_name }}</td>
              </ng-container>

              <ng-container matColumnDef="teacher">
                <th mat-header-cell *matHeaderCellDef>教师</th>
                <td mat-cell *matCellDef="let item">{{ item.teacher_name }}</td>
              </ng-container>

              <ng-container matColumnDef="time">
                <th mat-header-cell *matHeaderCellDef>时间</th>
                <td mat-cell *matCellDef="let item">周{{ item.day_of_week }} 第{{ item.period }}节</td>
              </ng-container>

              <ng-container matColumnDef="original_classroom">
                <th mat-header-cell *matHeaderCellDef>原教室</th>
                <td mat-cell *matCellDef="let item">{{ item.original_classroom_name }}</td>
              </ng-container>

              <ng-container matColumnDef="action">
                <th mat-header-cell *matHeaderCellDef>处置</th>
                <td mat-cell *matCellDef="let item">
                  <mat-chip [color]="getActionColor(item.action)" selected>
                    {{ item.action_display }}
                  </mat-chip>
                  <span *ngIf="item.action === 'relocate'"> → {{ item.new_classroom_name }}</span>
                </td>
              </ng-container>

              <ng-container matColumnDef="note">
                <th mat-header-cell *matHeaderCellDef>说明</th>
                <td mat-cell *matCellDef="let item">{{ item.note }}</td>
              </ng-container>

              <tr mat-header-row *matHeaderRowDef="itemColumns"></tr>
              <tr mat-row *matRowDef="let row; columns: itemColumns;"></tr>
            </table>
            <p *ngIf="(selected.items || []).length === 0">
              停用区间内该教室没有受影响的课程。
            </p>
          </mat-card-content>
        </mat-card>
      </div>
    </div>
  `
})
export class ClassroomSuspensionsComponent implements OnInit {
  displayedColumns: string[] = ['classroom', 'semester', 'date_range', 'reason', 'status', 'actions'];
  itemColumns: string[] = ['course', 'class', 'teacher', 'time', 'original_classroom', 'action', 'note'];
  dataSource: ClassroomSuspension[] = [];
  classrooms: Classroom[] = [];
  semesters: Semester[] = [];
  selected: ClassroomSuspension | null = null;
  showForm = false;
  submitting = false;
  confirmingId: number | null = null;
  restoringId: number | null = null;
  message = '';
  form: FormGroup;

  roomTypes: Record<string, string> = {
    normal: '普通教室',
    lab: '实验室',
    multimedia: '多媒体教室'
  };

  constructor(
    private api: ApiService,
    private fb: FormBuilder
  ) {
    this.form = this.fb.group({
      classroom: [null, Validators.required],
      semester: [null, Validators.required],
      start_date: ['', Validators.required],
      end_date: ['', Validators.required],
      reason: ['', Validators.required]
    });
  }

  ngOnInit(): void {
    this.loadData();
    this.api.getClassrooms().subscribe(data => this.classrooms = data);
    this.api.getSemesters().subscribe(data => {
      this.semesters = data;
      const active = data.find(s => s.is_active);
      if (active) this.form.patchValue({ semester: active.id });
    });
  }

  getRoomTypeLabel(type: string): string {
    return this.roomTypes[type] || type;
  }

  getStatusColor(status: string): string {
    const map: Record<string, string> = {
      draft: 'primary',
      applied: 'accent',
      blocked: 'warn',
      restored: ''
    };
    return map[status] ?? '';
  }

  getActionColor(action: string): string {
    const map: Record<string, string> = {
      relocate: 'primary',
      suspend: 'warn',
      blocked: 'warn'
    };
    return map[action] ?? '';
  }

  loadData(): void {
    this.api.getClassroomSuspensions().subscribe(data => {
      this.dataSource = data;
      if (this.selected) {
        this.selected = data.find(s => s.id === this.selected!.id) || null;
      }
    });
  }

  startCreate(): void {
    this.form.reset({ classroom: null, semester: null, start_date: '', end_date: '', reason: '' });
    const active = this.semesters.find(s => s.is_active);
    if (active) this.form.patchValue({ semester: active.id });
    this.showForm = true;
  }

  cancel(): void {
    this.showForm = false;
  }

  save(): void {
    if (this.form.invalid) {
      alert('请填写完整信息');
      return;
    }
    this.submitting = true;
    this.message = '';
    this.api.createClassroomSuspension(this.form.value).subscribe({
      next: data => {
        this.submitting = false;
        this.showForm = false;
        if (data.status === 'blocked') {
          this.message = `已登记，但存在无法安置的课程：${data.blocking_reason}。整批保持原课表，可调整后重新确认。`;
        } else {
          this.message = `登记成功，共 ${(data.items || []).length} 节受影响课程已匹配处置方案，请核对后点击"确认生效"。`;
        }
        this.loadData();
        this.selected = data;
      },
      error: err => {
        this.submitting = false;
        const detail = err.error ? JSON.stringify(err.error) : '登记失败';
        alert(`登记失败：${detail}`);
      }
    });
  }

  toggleDetail(item: ClassroomSuspension): void {
    if (this.selected?.id === item.id) {
      this.selected = null;
    } else {
      this.api.getClassroomSuspension(item.id).subscribe(data => {
        this.selected = data;
      });
    }
  }

  confirm(item: ClassroomSuspension): void {
    if (this.confirmingId !== null) return;
    this.confirmingId = item.id;
    this.message = '';
    this.api.confirmClassroomSuspension(item.id).subscribe({
      next: result => {
        this.confirmingId = null;
        this.message = result.message;
        this.loadData();
        this.selected = result.suspension;
      },
      error: err => {
        this.confirmingId = null;
        alert(err.error?.error || '确认失败');
        this.loadData();
      }
    });
  }

  restore(item: ClassroomSuspension): void {
    if (this.restoringId !== null) return;
    if (!confirm(`确定恢复教室 "${item.classroom_name}" 吗？恢复后该教室可重新参与排课。`)) {
      return;
    }
    this.restoringId = item.id;
    this.message = '';
    this.api.restoreClassroomSuspension(item.id).subscribe({
      next: result => {
        this.restoringId = null;
        this.message = result.message;
        this.loadData();
        this.selected = result.suspension;
      },
      error: err => {
        this.restoringId = null;
        alert(err.error?.error || '恢复失败');
        this.loadData();
      }
    });
  }
}
