import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { MatTableModule } from '@angular/material/table';
import { MatButtonModule } from '@angular/material/button';
import { MatInputModule } from '@angular/material/input';
import { MatSelectModule } from '@angular/material/select';
import { MatCardModule } from '@angular/material/card';
import { MatIconModule } from '@angular/material/icon';
import { MatChipsModule } from '@angular/material/chips';
import { MatProgressBarModule } from '@angular/material/progress-bar';
import { ApiService } from '../../services/api.service';
import type {
  Classroom, Semester, ClassroomSuspension,
  SuspensionPreview, SuspensionConfirmResult, SuspensionDisposition
} from '../../types';
import { HttpErrorResponse } from '@angular/common/http';

@Component({
  selector: 'app-classroom-suspensions',
  standalone: true,
  imports: [
    CommonModule,
    FormsModule,
    MatTableModule,
    MatButtonModule,
    MatInputModule,
    MatSelectModule,
    MatCardModule,
    MatIconModule,
    MatChipsModule,
    MatProgressBarModule
  ],
  template: `
    <div class="page-container">
      <h1 class="page-title">教室临时停用</h1>

      <mat-card class="form-card">
        <mat-card-header>
          <mat-card-title>登记停用</mat-card-title>
          <mat-card-subtitle>登记停用日期区间和原因后，系统将列出受影响课程并匹配替代教室</mat-card-subtitle>
        </mat-card-header>
        <mat-card-content>
          <div class="form-row">
            <mat-form-field appearance="outline" class="form-field">
              <mat-label>学期</mat-label>
              <mat-select [(ngModel)]="semesterId">
                <mat-option *ngFor="let s of semesters" [value]="s.id">
                  {{ s.name }}
                </mat-option>
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="form-field">
              <mat-label>停用教室</mat-label>
              <mat-select [(ngModel)]="classroomId">
                <mat-option *ngFor="let c of classrooms" [value]="c.id">
                  {{ c.name }}（{{ getRoomTypeLabel(c.room_type) }} / 容量{{ c.capacity }}）
                </mat-option>
              </mat-select>
            </mat-form-field>

            <mat-form-field appearance="outline" class="form-field date-field">
              <mat-label>开始日期</mat-label>
              <input matInput type="date" [(ngModel)]="startDate">
            </mat-form-field>

            <mat-form-field appearance="outline" class="form-field date-field">
              <mat-label>结束日期</mat-label>
              <input matInput type="date" [(ngModel)]="endDate">
            </mat-form-field>
          </div>

          <mat-form-field appearance="outline" class="full-field">
            <mat-label>停用原因</mat-label>
            <textarea matInput rows="2" [(ngModel)]="reason" placeholder="例如：教学楼检修、考试征用"></textarea>
          </mat-form-field>
        </mat-card-content>
        <mat-card-actions>
          <button mat-raised-button color="primary" (click)="register()" [disabled]="busy">
            <mat-icon>event_busy</mat-icon>
            登记并预览受影响课程
          </button>
          <button mat-button (click)="clearPreview()" *ngIf="preview">
            <mat-icon>close</mat-icon>
            关闭预览
          </button>
        </mat-card-actions>
      </mat-card>

      <mat-progress-bar *ngIf="busy" mode="indeterminate" style="margin: 12px 0;"></mat-progress-bar>

      <div *ngIf="message" class="message-bar" [class.message-warn]="message.type === 'warn'"
           [class.message-error]="message.type === 'error'" [class.message-success]="message.type === 'success'">
        <mat-icon>{{ message.type === 'success' ? 'check_circle' : message.type === 'error' ? 'cancel' : 'warning' }}</mat-icon>
        <span>{{ message.text }}</span>
      </div>

      <mat-card *ngIf="preview" class="preview-card">
        <mat-card-header>
          <mat-card-title>
            受影响课程预览（{{ preview.affected_count }} 门次）
          </mat-card-title>
          <mat-card-subtitle>
            {{ preview.suspension.classroom_name }}：
            {{ preview.suspension.start_date }} ~ {{ preview.suspension.end_date }}，
            原因：{{ preview.suspension.reason }}
          </mat-card-subtitle>
        </mat-card-header>

        <mat-card-content>
          <div *ngIf="preview.blocked" class="block-box">
            <div class="block-title">
              <mat-icon>block</mat-icon>
              存在无法安置的课程，确认时整批将保持原课表
            </div>
            <ul>
              <li *ngFor="let reason of preview.blocking_reasons">{{ reason }}</li>
            </ul>
          </div>

          <div class="table-container" *ngIf="preview.items.length > 0">
            <table mat-table [dataSource]="preview.items" class="mat-elevation-z2">
              <ng-container matColumnDef="course">
                <th mat-header-cell *matHeaderCellDef>课程 / 班级</th>
                <td mat-cell *matCellDef="let item">
                  <div style="font-weight: 500;">{{ item.entry.course_name }}</div>
                  <div class="muted">{{ item.entry.class_name }}</div>
                </td>
              </ng-container>

              <ng-container matColumnDef="teacher">
                <th mat-header-cell *matHeaderCellDef>教师</th>
                <td mat-cell *matCellDef="let item">{{ item.entry.teacher_name }}</td>
              </ng-container>

              <ng-container matColumnDef="position">
                <th mat-header-cell *matHeaderCellDef>时间</th>
                <td mat-cell *matCellDef="let item">
                  周{{ item.entry.day_of_week }} 第{{ item.entry.period }}节
                </td>
              </ng-container>

              <ng-container matColumnDef="status">
                <th mat-header-cell *matHeaderCellDef>状态</th>
                <td mat-cell *matCellDef="let item">
                  <mat-chip *ngIf="item.entry.is_locked" color="warn" selected>锁定</mat-chip>
                  <mat-chip *ngIf="item.block_reason && !item.entry.is_locked" color="warn" selected>无法安置</mat-chip>
                  <mat-chip *ngIf="!item.block_reason" color="primary" selected>可调整</mat-chip>
                </td>
              </ng-container>

              <ng-container matColumnDef="candidate">
                <th mat-header-cell *matHeaderCellDef>替代教室</th>
                <td mat-cell *matCellDef="let item">
                  <mat-form-field appearance="outline" *ngIf="item.candidates.length > 0 && !item.block_reason"
                                  class="candidate-select">
                    <mat-label>选择替代教室</mat-label>
                    <mat-select [ngModel]="selectedRooms[item.entry.id]"
                                (ngModelChange)="onRoomChange(item.entry.id, $event)">
                      <mat-option *ngFor="let room of item.candidates" [value]="room.id">
                        {{ room.name }}（容量{{ room.capacity }}）
                      </mat-option>
                    </mat-select>
                  </mat-form-field>
                  <span *ngIf="item.candidates.length === 0 && !item.block_reason" class="muted">无候选</span>
                </td>
              </ng-container>

              <ng-container matColumnDef="reason">
                <th mat-header-cell *matHeaderCellDef>阻断原因</th>
                <td mat-cell *matCellDef="let item" class="block-reason-cell">
                  {{ item.block_reason || '—' }}
                </td>
              </ng-container>

              <tr mat-header-row *matHeaderRowDef="previewColumns"></tr>
              <tr mat-row *matRowDef="let row; columns: previewColumns;"></tr>
            </table>
          </div>

          <p *ngIf="preview.items.length === 0" class="muted" style="padding: 12px 0;">
            停用区间内该教室没有排课，确认后停用仅对普通排课生效（自动排课不会占用该教室）。
          </p>
        </mat-card-content>

        <mat-card-actions *ngIf="preview.suspension.status === 'pending'">
          <button mat-raised-button color="primary" (click)="confirm('relocate')" [disabled]="busy">
            <mat-icon>swap_horiz</mat-icon>
            确认调整教室
          </button>
          <button mat-raised-button color="warn" (click)="confirm('cancel')" [disabled]="busy">
            <mat-icon>event_busy</mat-icon>
            确认全部停课
          </button>
        </mat-card-actions>
      </mat-card>

      <h2 style="margin-top: 28px;">停用记录</h2>
      <div class="table-container">
        <table mat-table [dataSource]="history" class="mat-elevation-z8" multiTemplateDataRows>
          <ng-container matColumnDef="classroom">
            <th mat-header-cell *matHeaderCellDef>教室</th>
            <td mat-cell *matCellDef="let item">{{ item.classroom_name }}</td>
          </ng-container>

          <ng-container matColumnDef="semester">
            <th mat-header-cell *matHeaderCellDef>学期</th>
            <td mat-cell *matCellDef="let item">{{ item.semester_name }}</td>
          </ng-container>

          <ng-container matColumnDef="range">
            <th mat-header-cell *matHeaderCellDef>停用区间</th>
            <td mat-cell *matCellDef="let item">{{ item.start_date }} ~ {{ item.end_date }}</td>
          </ng-container>

          <ng-container matColumnDef="reason">
            <th mat-header-cell *matHeaderCellDef>原因</th>
            <td mat-cell *matCellDef="let item">{{ item.reason }}</td>
          </ng-container>

          <ng-container matColumnDef="disposal">
            <th mat-header-cell *matHeaderCellDef>处置方式</th>
            <td mat-cell *matCellDef="let item">
              {{ item.disposal ? item.disposal_display : '—' }}
            </td>
          </ng-container>

          <ng-container matColumnDef="status">
            <th mat-header-cell *matHeaderCellDef>状态</th>
            <td mat-cell *matCellDef="let item">
              <mat-chip [color]="statusColor(item.status)" selected>
                {{ item.status_display }}
              </mat-chip>
            </td>
          </ng-container>

          <ng-container matColumnDef="actions">
            <th mat-header-cell *matHeaderCellDef>操作</th>
            <td mat-cell *matCellDef="let item" class="action-cell">
              <button mat-icon-button color="primary" (click)="openPreview(item, $event)"
                      [title]="item.status === 'pending' ? '预览/确认处置' : '查看处理结果'"
                      [disabled]="busy">
                <mat-icon>visibility</mat-icon>
              </button>
              <button mat-icon-button color="primary" *ngIf="item.status === 'applied'"
                      (click)="recover(item, $event)" title="恢复使用" [disabled]="busy">
                <mat-icon>restart_alt</mat-icon>
              </button>
              <button mat-icon-button color="warn" *ngIf="item.status === 'pending'"
                      (click)="remove(item, $event)" title="删除登记" [disabled]="busy">
                <mat-icon>delete</mat-icon>
              </button>
            </td>
          </ng-container>

          <ng-container matColumnDef="expandedDetail">
            <td mat-cell *matCellDef="let item" [attr.colspan]="historyColumns.length">
              <div *ngIf="expandedId === item.id && item.dispositions?.length" class="detail-box">
                <div class="detail-title">处理结果（{{ item.dispositions!.length }} 条，含历史原因）</div>
                <table class="detail-table">
                  <thead>
                    <tr>
                      <th>课程 / 班级</th>
                      <th>时间</th>
                      <th>处置</th>
                      <th>原教室</th>
                      <th>替代教室</th>
                      <th>停用原因</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr *ngFor="let d of item.dispositions">
                      <td>{{ d.course_name }} / {{ d.class_name }}</td>
                      <td>周{{ d.day_of_week }} 第{{ d.period }}节</td>
                      <td>
                        <mat-chip [color]="d.action === 'cancelled' ? 'warn' : 'primary'" selected>
                          {{ d.action_display }}
                        </mat-chip>
                      </td>
                      <td>{{ d.original_classroom_name }}</td>
                      <td>{{ d.new_classroom_name || '—' }}</td>
                      <td>{{ d.reason }}（{{ d.start_date }}~{{ d.end_date }}）</td>
                    </tr>
                  </tbody>
                </table>
              </div>
              <div *ngIf="expandedId === item.id && !item.dispositions?.length" class="detail-box muted">
                尚无处置记录
              </div>
            </td>
          </ng-container>

          <tr mat-header-row *matHeaderRowDef="historyColumns"></tr>
          <tr mat-row *matRowDef="let item; columns: historyColumns;"
              class="history-row" (click)="toggleExpand(item)"></tr>
          <tr mat-row *matRowDef="let item; columns: ['expandedDetail']" class="detail-row"></tr>
        </table>

        <p *ngIf="history.length === 0" class="muted" style="padding: 20px 0; text-align: center;">
          暂无教室停用记录。
        </p>
      </div>
    </div>
  `,
  styles: [`
    .form-card, .preview-card { margin-bottom: 20px; }
    .form-row { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 8px; }
    .form-field { flex: 1 1 220px; }
    .date-field { flex: 0 1 190px; }
    .full-field { width: 100%; }
    .candidate-select { width: 100%; }
    .muted { color: #888; font-size: 12px; }
    .message-bar {
      display: flex; align-items: center; gap: 8px;
      padding: 10px 14px; border-radius: 4px; margin: 12px 0;
      background: #e3f2fd; color: #0d47a1;
    }
    .message-warn { background: #fff8e1; color: #8d6e00; }
    .message-error { background: #ffebee; color: #b71c1c; }
    .message-success { background: #e8f5e9; color: #1b5e20; }
    .block-box {
      background: #ffebee; border-left: 4px solid #f44336;
      padding: 12px 16px; margin-bottom: 16px; border-radius: 4px;
    }
    .block-title { font-weight: 600; color: #b71c1c; display: flex; align-items: center; gap: 6px; }
    .block-box ul { margin: 8px 0 0 20px; color: #b71c1c; }
    .block-reason-cell { color: #b71c1c; font-size: 12px; }
    .history-row { cursor: pointer; }
    .detail-row { background: #fafafa; }
    .detail-box { padding: 12px 16px; overflow-x: auto; }
    .detail-title { font-weight: 600; margin-bottom: 8px; }
    .detail-table { width: 100%; border-collapse: collapse; font-size: 13px; }
    .detail-table th, .detail-table td { border: 1px solid #e0e0e0; padding: 6px 10px; text-align: left; }
    .detail-table th { background: #f5f5f5; }
  `]
})
export class ClassroomSuspensionsComponent implements OnInit {
  semesters: Semester[] = [];
  classrooms: Classroom[] = [];
  history: ClassroomSuspension[] = [];

  semesterId: number | null = null;
  classroomId: number | null = null;
  startDate = '';
  endDate = '';
  reason = '';

  preview: SuspensionPreview | null = null;
  selectedRooms: { [entryId: number]: number } = {};
  expandedId: number | null = null;
  busy = false;
  message: { type: 'success' | 'warn' | 'error'; text: string } | null = null;

  previewColumns = ['course', 'teacher', 'position', 'status', 'candidate', 'reason'];
  historyColumns = ['classroom', 'semester', 'range', 'reason', 'disposal', 'status', 'actions'];

  private roomTypes: Record<string, string> = {
    normal: '普通教室',
    lab: '实验室',
    multimedia: '多媒体教室'
  };

  constructor(private api: ApiService, private route: ActivatedRoute) {}

  ngOnInit(): void {
    this.api.getSemesters().subscribe(data => {
      this.semesters = data;
      const active = data.find(s => s.is_active) ?? data[0];
      if (active) this.semesterId = active.id;
      this.loadHistory();
    });
    this.api.getClassrooms().subscribe(data => {
      this.classrooms = data;
      this.route.queryParamMap.subscribe(params => {
        const preset = params.get('classroom');
        if (preset) this.classroomId = Number(preset);
      });
    });
  }

  getRoomTypeLabel(type: string): string {
    return this.roomTypes[type] || type;
  }

  statusColor(status: string): string {
    if (status === 'applied') return 'warn';
    if (status === 'recovered') return 'accent';
    return '';
  }

  loadHistory(): void {
    if (!this.semesterId) return;
    this.api.getClassroomSuspensions(undefined, this.semesterId).subscribe(data => {
      this.history = data;
    });
  }

  register(): void {
    this.message = null;
    if (!this.semesterId || !this.classroomId || !this.startDate || !this.endDate || !this.reason.trim()) {
      this.message = { type: 'warn', text: '请完整填写学期、教室、停用日期区间和原因' };
      return;
    }
    if (this.startDate > this.endDate) {
      this.message = { type: 'warn', text: '开始日期不能晚于结束日期' };
      return;
    }

    this.busy = true;
    this.api.createClassroomSuspension({
      classroom_id: this.classroomId,
      semester_id: this.semesterId,
      start_date: this.startDate,
      end_date: this.endDate,
      reason: this.reason.trim()
    }).subscribe({
      next: preview => {
        this.busy = false;
        this.preview = preview;
        this.selectedRooms = {};
        preview.items.forEach(item => {
          if (item.chosen_classroom_id !== null) {
            this.selectedRooms[item.entry.id] = item.chosen_classroom_id;
          }
        });
        this.message = preview.blocked
          ? { type: 'warn', text: '存在无法安置的课程，请先解除锁定或改用停课处置' }
          : { type: 'success', text: `已登记，共影响 ${preview.affected_count} 门次课程，请确认处置方式` };
        this.loadHistory();
      },
      error: err => {
        this.busy = false;
        this.preview = null;
        this.message = { type: 'error', text: this.formatError(err, '登记失败') };
      }
    });
  }

  openPreview(item: ClassroomSuspension, event: Event): void {
    event.stopPropagation();
    this.expandedId = item.id;
    this.message = null;

    // 已处置/已恢复的记录直接展开处理结果，不重新计算安置方案
    if (item.status !== 'pending') {
      this.preview = null;
      return;
    }

    this.busy = true;
    this.api.getSuspensionPreview(item.id).subscribe({
      next: preview => {
        this.busy = false;
        this.preview = preview;
        this.selectedRooms = {};
        preview.items.forEach(pi => {
          if (pi.chosen_classroom_id !== null) {
            this.selectedRooms[pi.entry.id] = pi.chosen_classroom_id;
          }
        });
      },
      error: err => {
        this.busy = false;
        this.message = { type: 'error', text: this.formatError(err, '加载预览失败') };
      }
    });
  }

  onRoomChange(entryId: number, roomId: number): void {
    this.selectedRooms[entryId] = roomId;
  }

  confirm(disposal: 'relocate' | 'cancel'): void {
    if (!this.preview) return;
    const id = this.preview.suspension.id;
    if (disposal === 'cancel' &&
        !confirm('确认将停用区间内所有受影响课程记为停课吗？')) {
      return;
    }

    const assignments: { [entryId: number]: number } = {};
    if (disposal === 'relocate') {
      for (const item of this.preview.items) {
        if (!item.block_reason) {
          assignments[item.entry.id] = this.selectedRooms[item.entry.id] ?? item.chosen_classroom_id!;
        }
      }
    }

    this.busy = true;
    this.message = null;
    this.api.confirmClassroomSuspension(
      id, disposal, disposal === 'relocate' ? assignments : undefined
    ).subscribe({
      next: (result: SuspensionConfirmResult) => {
        this.busy = false;
        if (result.already_applied) {
          this.message = { type: 'warn', text: '该停用登记已确认，本次为重复提交，处置结果只生效一次' };
        } else {
          const count = result.results?.length ?? 0;
          this.message = {
            type: 'success',
            text: disposal === 'relocate'
              ? `已一次性写入 ${count} 条替代教室安排`
              : `已一次性写入 ${count} 条停课记录`
          };
        }
        this.preview = null;
        this.loadHistory();
      },
      error: (err: HttpErrorResponse) => {
        this.busy = false;
        if (err.status === 409 && err.error?.blocking_reasons) {
          const reasons = err.error.blocking_reasons.join('；');
          this.message = {
            type: 'error',
            text: `处置已阻断，整批保持原课表：${reasons}`
          };
          if (this.preview) {
            // 刷新预览以反映最新阻断状态
            this.api.getSuspensionPreview(id).subscribe(p => {
              this.preview = p;
            });
          }
        } else {
          this.message = { type: 'error', text: this.formatError(err, '确认失败') };
        }
      }
    });
  }

  recover(item: ClassroomSuspension, event: Event): void {
    event.stopPropagation();
    if (!confirm(`确认恢复 ${item.classroom_name} 的使用吗？恢复后普通排课可重新占用该教室。`)) {
      return;
    }
    this.busy = true;
    this.api.recoverClassroomSuspension(item.id).subscribe({
      next: result => {
        this.busy = false;
        this.message = {
          type: 'success',
          text: result.already_recovered ? '该教室此前已恢复使用' : (result.message || '教室已恢复使用')
        };
        if (this.preview?.suspension.id === item.id) this.clearPreview();
        this.loadHistory();
      },
      error: err => {
        this.busy = false;
        this.message = { type: 'error', text: this.formatError(err, '恢复失败') };
      }
    });
  }

  remove(item: ClassroomSuspension, event: Event): void {
    event.stopPropagation();
    if (!confirm(`确定删除 ${item.classroom_name} 的待确认停用登记吗？`)) {
      return;
    }
    this.busy = true;
    this.api.deleteClassroomSuspension(item.id).subscribe({
      next: () => {
        this.busy = false;
        this.message = { type: 'success', text: '停用登记已删除' };
        if (this.preview?.suspension.id === item.id) this.clearPreview();
        this.loadHistory();
      },
      error: err => {
        this.busy = false;
        this.message = { type: 'error', text: this.formatError(err, '删除失败') };
      }
    });
  }

  toggleExpand(item: ClassroomSuspension): void {
    this.expandedId = this.expandedId === item.id ? null : item.id;
  }

  clearPreview(): void {
    this.preview = null;
    this.selectedRooms = {};
  }

  private formatError(err: HttpErrorResponse | any, fallback: string): string {
    if (err?.error) {
      if (typeof err.error === 'string') return err.error;
      if (err.error.error) return err.error.error;
      const field = Object.values(err.error)[0];
      if (Array.isArray(field)) return field.join('；');
      if (typeof field === 'string') return field;
    }
    return fallback;
  }
}
