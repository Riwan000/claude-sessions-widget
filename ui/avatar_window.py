"""Always-on-top desktop avatar companion (Baymax) roaming along the taskbar.

Draws procedural pixel/vector Baymax with custom Big Hero 6 mechanics:
- Normal mode: classic white marshmallow body, slow waddle along the taskbar.
- Idle mode: returns to Malachite & Spruce recharge station box and deflates to sleep.
- Wake up: inflates up out of the recharge station when sessions start.
- Working mode: sweeping vertical acid citron / white scan beam while tools run.
- Alert mode: suits up in Malachite & Spruce superhero mech armor with jet thrusters,
  flying and hovering along the taskbar (no walking or jumping).
- Clean matte speech bubble: tracks Baymax in real time without glow.
- Clicking Baymax toggles the full multi-task session window directly above his head.
"""

import math
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPolygon,
    QPolygonF,
)
from PySide6.QtWidgets import QApplication, QWidget

# Animation & physics constants
TICK_INTERVAL_MS = 16  # 60 FPS for smooth pacing
SLOW_WADDLE_SPEED = 0.35  # calm, slow signature waddle
FLIGHT_SPEED = 0.55  # calm, majestic flight patrol
RECHARGE_SPEED = 0.65  # walking back to recharge box
WADDLE_FREQ = 0.045  # slow waddling sway frequency
SCAN_CYCLE_SPEED = 0.05  # speed of vertical code scan beam

# Window dimensions for the roaming avatar
AVATAR_WINDOW_WIDTH = 190
AVATAR_WINDOW_HEIGHT = 120


class AvatarWindow(QWidget):
    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(AVATAR_WINDOW_WIDTH, AVATAR_WINDOW_HEIGHT)

        # State machine:
        # 'in_box_sleeping' | 'inflating_out' | 'waddling' | 'working' |
        # 'permission' (flying mech suit) | 'walking_to_box' | 'deflating_in'
        self._state = "in_box_sleeping"
        self._frame = 0
        self._transition_prog = 0.0

        # Movement tracking
        self._dir = 1
        self._screen_x = 100
        self._home_x = 60
        self._min_x = 40
        self._max_x = 600

        # Session data
        self._sessions = []
        self._current_task_text = ""
        self._current_task_sub = ""
        self._current_task_icon = "⚪"
        self._bubble_visible = True
        self._paused = False

        # Ticker rotation for multi-task preview in the bubble
        self._ticker_index = 0
        self._ticker_timer = QTimer(self)
        self._ticker_timer.timeout.connect(self._rotate_ticker)
        self._ticker_timer.start(3000)

        # Drag and drop tracking
        self._is_dragging = False
        self._drag_start_pos = None
        self._drag_offset = None
        self._target_ground_y = 0
        self._last_moved_x = None
        self._last_moved_y = None

        # Main animation & physics loop
        self._anim_timer = QTimer(self)
        self._anim_timer.timeout.connect(self._tick)
        self._anim_timer.start(TICK_INTERVAL_MS)

        app = QApplication.instance()
        if app:
            app.screenAdded.connect(self._on_screens_changed)
            app.screenRemoved.connect(self._on_screens_changed)

        self._reposition_to_taskbar()

    def _recalculate_screen_bounds(self):
        screens = QApplication.screens()
        if not screens:
            return
        self._min_x = min(s.availableGeometry().x() for s in screens) + 40
        self._max_x = max(s.availableGeometry().x() + s.availableGeometry().width() for s in screens) - AVATAR_WINDOW_WIDTH - 40

    def _on_screens_changed(self, _screen=None):
        self._recalculate_screen_bounds()
        self._reposition_to_taskbar()

    def _reposition_to_taskbar(self):
        self._recalculate_screen_bounds()
        screen = QApplication.primaryScreen()
        if screen is None:
            return
        avail = screen.availableGeometry()
        self._home_x = avail.x() + 60
        self._ground_y = avail.y() + avail.height() - AVATAR_WINDOW_HEIGHT
        self._target_ground_y = self._ground_y

        if self._state == "in_box_sleeping":
            self._screen_x = self._home_x
        self._last_moved_x = int(round(self._screen_x))
        self._last_moved_y = int(round(self._ground_y))
        self.move(self._last_moved_x, self._last_moved_y)

    def set_sessions(self, sessions):
        self._sessions = sessions or []
        self._evaluate_state()

    def set_paused(self, paused):
        self._paused = paused

    def is_paused(self):
        return self._paused

    def set_bubble_visible(self, visible):
        self._bubble_visible = bool(visible)
        self.update()

    def _evaluate_state(self):
        has_permission = any(s.display_status == "permission" for s in self._sessions)
        has_running = any(s.display_status == "running" for s in self._sessions)
        has_active = bool(self._sessions)

        if has_permission:
            if self._state in ("in_box_sleeping", "deflating_in"):
                self._state = "inflating_out"
                self._transition_prog = 0.0
            else:
                self._state = "permission"
        elif has_running:
            if self._state in ("in_box_sleeping", "deflating_in"):
                self._state = "inflating_out"
                self._transition_prog = 0.0
            elif self._state not in ("inflating_out", "deflating_in"):
                self._state = "working"
        elif has_active:
            if self._state in ("in_box_sleeping", "deflating_in"):
                self._state = "inflating_out"
                self._transition_prog = 0.0
            elif self._state not in ("inflating_out", "deflating_in"):
                self._state = "waddling"
        else:
            # Zero active sessions left - return to recharge station
            if self._state not in ("walking_to_box", "deflating_in", "in_box_sleeping"):
                self._state = "walking_to_box"

        self._update_bubble_text()

    def _rotate_ticker(self):
        if not self._sessions:
            return
        # Priority to permission sessions
        perm_sessions = [s for s in self._sessions if s.display_status == "permission"]
        if perm_sessions:
            s = perm_sessions[0]
            self._current_task_icon = "⚠️"
            self._current_task_text = f"{s.project} (Permission)"
            detail = s.current_tool_detail or s.task or s.prompt or "Approval needed"
            self._current_task_sub = detail[:32] + "…" if len(detail) > 32 else detail
            return

        self._ticker_index = (self._ticker_index + 1) % len(self._sessions)
        s = self._sessions[self._ticker_index]
        idx_str = f"[{self._ticker_index + 1}/{len(self._sessions)}]"

        if s.display_status == "running":
            self._current_task_icon = "⚡"
            self._current_task_text = f"{s.project} {idx_str}"
            detail = s.current_tool_detail or "Running tool..."
            self._current_task_sub = detail[:32] + "…" if len(detail) > 32 else detail
        elif s.display_status == "finished":
            self._current_task_icon = "✅"
            self._current_task_text = f"{s.project} {idx_str}"
            self._current_task_sub = "Task finished"
        else:
            self._current_task_icon = "⚪"
            self._current_task_text = f"{s.project} {idx_str}"
            self._current_task_sub = "Idle session"

    def _update_bubble_text(self):
        if self._state == "in_box_sleeping":
            self._current_task_icon = "💤"
            self._current_task_text = "Recharging inside station"
            self._current_task_sub = "Waiting for next AI prompt"
        elif self._state == "walking_to_box":
            self._current_task_icon = "🔌"
            self._current_task_text = "Going to recharge"
            self._current_task_sub = "Heading into station"
        elif self._state == "inflating_out":
            self._current_task_icon = "🔋"
            self._current_task_text = "Waking up..."
            self._current_task_sub = "Inflating and deploying"
        elif self._sessions:
            self._rotate_ticker()

    def _tick(self):
        self._frame += 1

        if not self._paused and not self._is_dragging:
            if self._state == "permission":
                # Smooth flight glide
                self._screen_x += self._dir * FLIGHT_SPEED
                if self._screen_x > self._max_x:
                    self._dir = -1
                elif self._screen_x < self._min_x:
                    self._dir = 1
            elif self._state == "waddling":
                # Gentle slow waddle
                self._screen_x += self._dir * SLOW_WADDLE_SPEED
                if self._screen_x > self._max_x:
                    self._dir = -1
                elif self._screen_x < self._min_x:
                    self._dir = 1
            elif self._state == "working":
                # Subtly scanning pacing
                self._screen_x += self._dir * (SLOW_WADDLE_SPEED * 0.5)
                if self._screen_x > self._max_x:
                    self._dir = -1
                elif self._screen_x < self._min_x:
                    self._dir = 1
            elif self._state == "walking_to_box":
                if self._screen_x > self._home_x:
                    self._dir = -1
                    self._screen_x -= RECHARGE_SPEED
                    if self._screen_x <= self._home_x:
                        self._screen_x = self._home_x
                        self._state = "deflating_in"
                        self._transition_prog = 0.0
                else:
                    self._dir = 1
                    self._screen_x += RECHARGE_SPEED
                    if self._screen_x >= self._home_x:
                        self._screen_x = self._home_x
                        self._state = "deflating_in"
                        self._transition_prog = 0.0
            elif self._state == "deflating_in":
                self._transition_prog += 0.012
                if self._transition_prog >= 1.0:
                    self._transition_prog = 1.0
                    self._state = "in_box_sleeping"
                    self._update_bubble_text()
            elif self._state == "inflating_out":
                self._transition_prog += 0.015
                if self._transition_prog >= 1.0:
                    self._transition_prog = 1.0
                    self._state = "waddling"
                    self._dir = 1
                    self._update_bubble_text()

            # Dynamic ground adjustment: query screen every 45 frames (~0.75s) to eliminate DWM IPC overhead
            if self._frame % 45 == 0:
                center_x = int(self._screen_x + (AVATAR_WINDOW_WIDTH // 2))
                center_y = int(self._ground_y + (AVATAR_WINDOW_HEIGHT // 2))
                cur_screen = QApplication.screenAt(QPoint(center_x, center_y)) or QApplication.primaryScreen()
                if cur_screen:
                    avail = cur_screen.availableGeometry()
                    self._target_ground_y = avail.y() + avail.height() - AVATAR_WINDOW_HEIGHT

            if abs(self._ground_y - self._target_ground_y) > 0.5:
                self._ground_y += (self._target_ground_y - self._ground_y) * 0.1
            else:
                self._ground_y = self._target_ground_y

            # Only call OS move when integer pixel position actually changes
            ix = int(round(self._screen_x))
            iy = int(round(self._ground_y))
            if (ix, iy) != (self._last_moved_x, self._last_moved_y):
                self._last_moved_x = ix
                self._last_moved_y = iy
                self.move(ix, iy)

        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_start_pos = event.globalPosition().toPoint()
            self._drag_offset = event.position().toPoint()
            self._is_dragging = False
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (event.buttons() & Qt.LeftButton) and self._drag_start_pos is not None:
            dist = (event.globalPosition().toPoint() - self._drag_start_pos).manhattanLength()
            if not self._is_dragging and dist > 6:
                self._is_dragging = True

            if self._is_dragging:
                new_pos = event.globalPosition().toPoint() - self._drag_offset
                self.move(new_pos)
                self._screen_x = self.x()
                self._ground_y = self.y()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if self._is_dragging:
                self._is_dragging = False
                drop_pos = event.globalPosition().toPoint()
                target_screen = QApplication.screenAt(drop_pos) or QApplication.primaryScreen()
                if target_screen:
                    avail = target_screen.availableGeometry()
                    self._ground_y = avail.y() + avail.height() - AVATAR_WINDOW_HEIGHT
                    self._target_ground_y = self._ground_y
                    clamped_x = max(avail.x() + 10, min(self.x(), avail.x() + avail.width() - AVATAR_WINDOW_WIDTH - 10))
                    self._screen_x = clamped_x
                    self._last_moved_x = int(round(self._screen_x))
                    self._last_moved_y = int(round(self._ground_y))
                    self.move(self._last_moved_x, self._last_moved_y)

                    if self._state in ("in_box_sleeping", "deflating_in"):
                        self._home_x = self._screen_x
                self._drag_start_pos = None
                self._drag_offset = None
            else:
                self._drag_start_pos = None
                self._drag_offset = None
                self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)

        # Center Baymax horizontally in window
        char_center_x = self.width() // 2
        ground_line_y = self.height() - 8
        scale = 2.4

        # 1. Draw Clean Matte Speech Bubble (Directly above him, zero glow)
        if self._bubble_visible and self._current_task_text:
            self._draw_speech_bubble(painter, char_center_x, ground_line_y)

        # 2. Draw Recharge Box (Drawn when sleeping, deflating, or inflating)
        if self._state in ("in_box_sleeping", "deflating_in", "inflating_out") or (
            self._state == "walking_to_box" and abs(self._screen_x - self._home_x) < 40
        ):
            self._draw_recharge_box(painter, char_center_x, ground_line_y, scale)

        # 3. Draw Baymax (Normal Inflatable or Armored Mech Suit)
        if self._state != "in_box_sleeping" or self._transition_prog < 1.0:
            painter.save()
            painter.translate(char_center_x, ground_line_y)
            if self._dir == -1 and self._state != "in_box_sleeping":
                painter.scale(-1, 1)

            if self._state == "permission":
                self._draw_armored_flight_baymax(painter, scale)
            else:
                self._draw_white_inflatable_baymax(painter, scale)

            painter.restore()

        painter.end()

    # --- SPEECH BUBBLE (CLEAN MATTE CARD, TRACKS HIM, ZERO GLOW) ---
    def _draw_speech_bubble(self, p, cx, ground_y):
        bubble_w = 176
        bubble_h = 28
        bubble_x = cx - (bubble_w // 2)
        bubble_y = 6 if self._state == "permission" else 14

        p.save()
        p.setRenderHint(QPainter.Antialiasing, True)

        # Clean matte card background (#061A14 deep spruce)
        p.setBrush(QColor(6, 26, 20, 245))
        p.setPen(QColor(0, 168, 107, 180))  # malachite border
        p.drawRoundedRect(QRectF(bubble_x, bubble_y, bubble_w, bubble_h), 6.0, 6.0)

        # Downward pointer arrow
        arrow = QPolygonF([
            QPointF(cx - 4, bubble_y + bubble_h),
            QPointF(cx + 4, bubble_y + bubble_h),
            QPointF(cx, bubble_y + bubble_h + 4),
        ])
        p.drawPolygon(arrow)

        # Text rendering
        p.setPen(Qt.NoPen)
        font_title = QFont("Segoe UI", 8)
        font_title.setBold(True)
        p.setFont(font_title)

        # Icon & Title
        p.setPen(QColor("#ffffff"))
        p.drawText(bubble_x + 8, bubble_y + 12, f"{self._current_task_icon}  {self._current_task_text}")

        # Subtitle
        font_sub = QFont("Segoe UI", 7)
        p.setFont(font_sub)
        p.setPen(QColor("#a7f3d0"))
        p.drawText(bubble_x + 22, bubble_y + 23, self._current_task_sub)

        p.restore()

    # --- RECHARGE STATION (MALACHITE, SPRUCE & WHITE) ---
    def _draw_recharge_box(self, p, cx, ground_y, s):
        p.save()
        p.translate(cx, ground_y)

        # Deep Spruce Frame
        p.fillRect(QRectF(-10*s, -22*s, 20*s, 22*s), QColor("#061a14"))
        p.fillRect(QRectF(-8*s, -20*s, 16*s, 19*s), QColor("#0a2e23"))
        p.fillRect(QRectF(-10*s, -22*s, 20*s, 2*s), QColor("#00a86b"))
        p.fillRect(QRectF(-10*s, -22*s, 2*s, 22*s), QColor("#00a86b"))
        p.fillRect(QRectF(8*s, -22*s, 2*s, 22*s), QColor("#00a86b"))

        # Top indicator bar
        is_charging = (self._state in ("in_box_sleeping", "deflating_in"))
        p.fillRect(QRectF(-3*s, -21*s, 6*s, 1*s), QColor("#ffffff" if is_charging else "#00a86b"))

        # Power Bars
        p.fillRect(QRectF(-6*s, -18*s, 2*s, 2*s), QColor("#ffffff" if is_charging else "#144636"))
        p.fillRect(QRectF(-6*s, -15*s, 2*s, 2*s), QColor("#ffffff" if (is_charging and (self._frame // 15) % 3 > 0) else "#144636"))
        p.fillRect(QRectF(-6*s, -12*s, 2*s, 2*s), QColor("#ffffff" if (is_charging and (self._frame // 15) % 3 > 1) else "#144636"))

        # Baymax sleeping inside box
        if self._state == "in_box_sleeping":
            sleep_breath = math.sin(self._frame * 0.04) * 1.0
            p.fillRect(QRectF(-6*s, (-14*s) + sleep_breath, 12*s, 13*s), QColor("#ffffff"))
            p.fillRect(QRectF(-3*s, (-11*s) + sleep_breath, 2*s, 1*s), QColor("#0f172a"))
            p.fillRect(QRectF(1*s, (-11*s) + sleep_breath, 2*s, 1*s), QColor("#0f172a"))
            p.fillRect(QRectF(-1*s, (-11*s) + sleep_breath, 2*s, 1*s), QColor("#0f172a"))

            # Floating z
            z_prog = (self._frame % 55) / 55.0
            p.setFont(QFont("Consolas", 8, QFont.Bold))
            p.setPen(QColor("#ffffff"))
            p.drawText(int(2 * s), int(-18 * s - (z_prog * 8)), "z")

        p.fillRect(QRectF(-11*s, -2*s, 22*s, 2*s), QColor("#03100c"))
        p.restore()

    # --- NORMAL WHITE INFLATABLE BAYMAX (EXACT PLANNED ART) ---
    def _draw_white_inflatable_baymax(self, p, s):
        is_waddling = (self._state in ("waddling", "walking_to_box", "working"))
        waddle_freq = 0.03 if self._state == "working" else 0.045
        waddle = math.sin(self._frame * waddle_freq) * 0.06 if is_waddling else 0.0
        bounce = abs(math.sin(self._frame * waddle_freq)) * -1.5 if is_waddling else 0.0
        y = bounce

        inflate_scale_y = 1.0
        inflate_scale_x = 1.0
        if self._state == "deflating_in":
            inflate_scale_y = 1.0 - (self._transition_prog * 0.45)
            inflate_scale_x = 1.0 - (self._transition_prog * 0.15)
            y += self._transition_prog * 6.0
        elif self._state == "inflating_out":
            inflate_scale_y = 0.55 + (self._transition_prog * 0.45)
            inflate_scale_x = 0.85 + (self._transition_prog * 0.15)
            y += (1.0 - self._transition_prog) * 6.0

        p.scale(inflate_scale_x, inflate_scale_y)
        p.rotate(waddle * 57.2958)

        # Slate shadow outline
        p.fillRect(QRectF(-7*s, (-16*s)+y, 14*s, 16*s), QColor("#64748b"))

        # Pure marshmallow white body
        p.fillRect(QRectF(-6*s, (-15*s)+y, 12*s, 14*s), QColor("#ffffff"))
        p.fillRect(QRectF(-7*s, (-10*s)+y, 14*s, 8*s), QColor("#ffffff"))

        # Underbelly soft 3D shading
        p.fillRect(QRectF(-6*s, (-4*s)+y, 12*s, 2*s), QColor("#e2e8f0"))

        # Head outline & fill
        p.fillRect(QRectF(-6*s, (-21*s)+y, 12*s, 7*s), QColor("#64748b"))
        p.fillRect(QRectF(-5*s, (-20*s)+y, 10*s, 6*s), QColor("#ffffff"))

        # Iconic ( •—• ) Face
        p.fillRect(QRectF(-3*s, (-18*s)+y, 2*s, 2*s), QColor("#0f172a"))
        p.fillRect(QRectF(1*s, (-18*s)+y, 2*s, 2*s), QColor("#0f172a"))
        p.fillRect(QRectF(-1*s, (-17*s)+y, 2*s, 1*s), QColor("#0f172a"))

        # Chest healthcare port (Upper left chest)
        port_color = QColor("#00a86b" if self._state == "working" else "#94a3b8")
        p.fillRect(QRectF(2*s, (-13*s)+y, 2*s, 2*s), port_color)

        # Inflatable Arms
        sway = math.sin(self._frame * waddle_freq) * 1.5 if is_waddling else 0.0
        p.fillRect(QRectF((-8*s)-sway, (-11*s)+y, 2*s, 7*s), QColor("#f1f5f9"))
        p.fillRect(QRectF((6*s)+sway, (-11*s)+y, 2*s, 7*s), QColor("#f1f5f9"))

        # Waddling Legs
        if is_waddling:
            step_l = 2.0 if (math.sin(self._frame * waddle_freq) > 0) else 0.0
            step_r = 2.0 if (math.sin(self._frame * waddle_freq) <= 0) else 0.0
            p.fillRect(QRectF(-5*s, (-2*s)+y+step_l, 4*s, 3*s), QColor("#e2e8f0"))
            p.fillRect(QRectF(1*s, (-2*s)+y+step_r, 4*s, 3*s), QColor("#e2e8f0"))
        else:
            p.fillRect(QRectF(-5*s, (-2*s)+y, 4*s, 3*s), QColor("#e2e8f0"))
            p.fillRect(QRectF(1*s, (-2*s)+y, 4*s, 3*s), QColor("#e2e8f0"))

        # Ethereal vertical scanning beam (ONLY when working / running tools)
        if self._state == "working":
            scan_height = 19.0 * s
            scan_y_start = (-20.0 * s) + y
            scan_cycle = (math.sin(self._frame * 0.05) + 1.0) / 2.0
            current_scan_y = scan_y_start + (scan_cycle * scan_height)
            beam_width = (12.0 * s) if (current_scan_y < (-14.0 * s) + y) else (16.0 * s)
            beam_x = -(beam_width / 2.0)

            p.fillRect(QRectF(beam_x, current_scan_y - (2.0 * s), beam_width, 5.0 * s), QColor(0, 168, 107, 45))
            p.fillRect(QRectF(beam_x - (1.0 * s), current_scan_y, beam_width + (2.0 * s), 1.5 * s), QColor(255, 255, 255, 220))
            p.fillRect(QRectF(beam_x + (2.0 * s), current_scan_y, beam_width - (4.0 * s), 0.8 * s), QColor("#ffffff"))

    # --- ARMORED SUPERHERO MECH SUIT (EXACT PLANNED ART) ---
    def _draw_armored_flight_baymax(self, p, s):
        flight_alt = -9.0 * s
        hover = math.sin(self._frame * 0.05) * (1.0 * s)
        y = flight_alt + hover

        p.rotate(0.06 * 57.2958)

        # Thruster flames
        flame_len = 6.0 * s if ((self._frame // 8) % 2 == 0) else 7.0 * s
        p.fillRect(QRectF(-4*s, (2*s)+y, 3*s, flame_len), QColor("#a7f3d0"))
        p.fillRect(QRectF(1*s, (2*s)+y, 3*s, flame_len), QColor("#a7f3d0"))
        p.fillRect(QRectF(-3*s, (2*s)+y, 1*s, flame_len - (2*s)), QColor("#ffffff"))
        p.fillRect(QRectF(2*s, (2*s)+y, 1*s, flame_len - (2*s)), QColor("#ffffff"))

        # Wing flames
        wing_flame = 4.0 * s if ((self._frame // 8) % 2 == 0) else 5.0 * s
        p.fillRect(QRectF(-12*s, (-8*s)+y, 2*s, wing_flame), QColor("#a7f3d0"))
        p.fillRect(QRectF(10*s, (-8*s)+y, 2*s, wing_flame), QColor("#a7f3d0"))

        # Spruce wings
        p.fillRect(QRectF(-11*s, (-17*s)+y, 3*s, 8*s), QColor("#061a14"))
        p.fillRect(QRectF(8*s, (-17*s)+y, 3*s, 8*s), QColor("#061a14"))

        # Malachite wing panels
        p.fillRect(QRectF(-14*s, (-22*s)+y, 3*s, 13*s), QColor("#00a86b"))
        p.fillRect(QRectF(11*s, (-22*s)+y, 3*s, 13*s), QColor("#00a86b"))
        p.fillRect(QRectF(-15*s, (-24*s)+y, 2*s, 3*s), QColor("#ffffff"))
        p.fillRect(QRectF(13*s, (-24*s)+y, 2*s, 3*s), QColor("#ffffff"))

        # Torso chassis outline
        p.fillRect(QRectF(-7*s, (-16*s)+y, 14*s, 16*s), QColor("#03100c"))

        # Malachite armor body
        p.fillRect(QRectF(-6*s, (-15*s)+y, 12*s, 14*s), QColor("#00a86b"))
        p.fillRect(QRectF(-7*s, (-10*s)+y, 14*s, 8*s), QColor("#00a86b"))

        # Deep spruce abdominal armor
        p.fillRect(QRectF(-5*s, (-8*s)+y, 10*s, 6*s), QColor("#061a14"))
        p.fillRect(QRectF(-4*s, (-4*s)+y, 8*s, 2*s), QColor("#0a2e23"))

        # Glowing white chest arc reactor
        pulse_white = QColor("#ffffff" if (self._frame // 15) % 2 == 0 else "#a7f3d0")
        p.fillRect(QRectF(-2*s, (-7*s)+y, 4*s, 4*s), pulse_white)

        # Pauldrons
        p.fillRect(QRectF(-9*s, (-16*s)+y, 4*s, 5*s), QColor("#00a86b"))
        p.fillRect(QRectF(5*s, (-16*s)+y, 4*s, 5*s), QColor("#00a86b"))
        p.fillRect(QRectF(-9*s, (-17*s)+y, 4*s, 1*s), QColor("#061a14"))
        p.fillRect(QRectF(5*s, (-17*s)+y, 4*s, 1*s), QColor("#061a14"))

        # Armored helmet
        p.fillRect(QRectF(-6*s, (-22*s)+y, 12*s, 8*s), QColor("#00a86b"))
        p.fillRect(QRectF(-6*s, (-23*s)+y, 12*s, 1*s), QColor("#061a14"))
        p.fillRect(QRectF(-6*s, (-19*s)+y, 1*s, 5*s), QColor("#061a14"))
        p.fillRect(QRectF(5*s, (-19*s)+y, 1*s, 5*s), QColor("#061a14"))
        p.fillRect(QRectF(-5*s, (-20*s)+y, 10*s, 5*s), QColor("#061a14"))

        # Glowing white ( •—• ) HUD eyes inside visor
        p.fillRect(QRectF(-3*s, (-18*s)+y, 2*s, 2*s), QColor("#ffffff"))
        p.fillRect(QRectF(1*s, (-18*s)+y, 2*s, 2*s), QColor("#ffffff"))
        p.fillRect(QRectF(-1*s, (-17*s)+y, 2*s, 1*s), QColor("#ffffff"))

        # Flight arms
        p.fillRect(QRectF(6*s, (-14*s)+y, 6*s, 4*s), QColor("#00a86b"))
        p.fillRect(QRectF(12*s, (-13*s)+y, 2*s, 2*s), QColor("#ffffff"))
        p.fillRect(QRectF(-9*s, (-12*s)+y, 3*s, 6*s), QColor("#00a86b"))

        # Boots
        p.fillRect(QRectF(-5*s, (-2*s)+y, 4*s, 4*s), QColor("#061a14"))
        p.fillRect(QRectF(1*s, (-2*s)+y, 4*s, 4*s), QColor("#061a14"))
        p.fillRect(QRectF(-5*s, (0*s)+y, 4*s, 2*s), QColor("#00a86b"))
        p.fillRect(QRectF(1*s, (0*s)+y, 4*s, 2*s), QColor("#00a86b"))
        p.fillRect(QRectF(-4*s, (2*s)+y, 3*s, 1*s), QColor("#144636"))
        p.fillRect(QRectF(1*s, (2*s)+y, 3*s, 1*s), QColor("#144636"))
