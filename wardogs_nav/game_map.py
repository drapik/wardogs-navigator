"""Large-map UI and actions; view coordinates never become vehicle fixes."""
import time
from PySide6.QtCore import QTimer
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QCheckBox,QFormLayout,QKeySequenceEdit,QLineEdit,QComboBox
from . import hotkeys
from .bigmap import BigMapOverlay
from .dialogs import number
from .ru import count
from .maps import map_info
from .routing import nearest_on_route
from .coordinates import parse_coordinates,format_coordinates,game_to_map,map_to_game


class GameMapController:
    def init_game_map(self):
        self.big_view=None;self.coordinate_view=None;self.game_window=0;self.big_resume=None
        self.big_tracking=False
        self.big_message='Большая карта · горячие клавиши готовы';self.big_message_until=0
        self.big_overlay=BigMapOverlay(self.settings['bigmap'])
        self.big_overlay.mask_changed.connect(lambda mask:setattr(self.worker,'map_mask',mask))
        self.cursor_timer=QTimer(self);self.cursor_timer.setInterval(100);self.cursor_timer.timeout.connect(self.poll_map_cursor)
        self.global_hotkeys=hotkeys.GlobalHotkeys(self)
        self.global_hotkeys.enabled_when=self.game_map_active
        self.global_hotkeys.triggered.connect(self.on_map_hotkey)
        self.global_hotkeys.status.connect(self.hotkey_status_changed)
        self.global_hotkeys.configure(self.settings['bigmap_hotkeys'])
        self.worker.map_result.connect(self.on_big_map)
        self.worker.action_failed.connect(self.map_feedback)

    def build_coordinate_input(self,layout):
        group=self.group('Игровые координаты',layout)
        group.addWidget(self.text('Пересчёт по карте, выбранной вверху. Введите или вставьте x87.18, y33.31; формат 87.18/33.31 тоже подходит.',True))
        self.coordinate_input=QLineEdit();self.coordinate_input.setPlaceholderText('x87.18, y33.31');group.addWidget(self.coordinate_input)
        self.coordinate_action=QComboBox()
        for action,label in [('destination','Сделать целью'),('start','Сделать стартом'),('waypoint','Добавить промежуточную точку'),('navigate','Новая навигация')]:
            self.coordinate_action.addItem(label,action)
        group.addWidget(self.row(self.coordinate_action,self.button('Применить координаты',self.apply_coordinates)))
        self.coordinate_input.returnPressed.connect(self.apply_coordinates)
        self.coordinate_feedback=self.text('Цель можно задать заранее; после возврата к мини-карте будет определено положение машины и начнётся навигация.',True);group.addWidget(self.coordinate_feedback)

    def apply_coordinates(self):
        try:
            game=parse_coordinates(self.coordinate_input.text())
            point=game_to_map(self.project['map'],game)
        except ValueError as error:
            self.coordinate_feedback.setText(str(error));return
        action=self.coordinate_action.currentData()
        self.apply_map_action(action,point)
        self.coordinate_feedback.setText(f'{map_info(self.project["map"])["name"]} · {format_coordinates(game)} · применено: {self.coordinate_action.currentText()}')
        if action=='navigate':self.start_navigation()

    def show_map_coordinates(self,x,y):
        self.coordinate_label.setText('Игровые координаты  '+format_coordinates(map_to_game(self.project['map'],[x,y])))

    def build_game_map_settings(self,layout):
        group=self.group('Большая карта в игре',layout)
        group.addWidget(self.text('Сначала откройте большую карту в игре, затем отдельно выделите её область. Пока мини-карта определяет положение, распознавание большой карты и чтение мыши не работают.',True))
        group.addWidget(self.button('Выделить большую карту на этом экране',lambda:self.select_region(large=True)))
        self.big_enabled=QCheckBox('Включить распознавание большой карты, наложение и горячие клавиши')
        self.big_enabled.setChecked(self.settings['bigmap']['enabled'])
        self.big_enabled.toggled.connect(lambda v:self.big_setting('enabled',v));group.addWidget(self.big_enabled)
        self.big_roads=QCheckBox('Показывать сеть дорог на большой карте')
        self.big_roads.setChecked(self.settings['bigmap']['show_roads'])
        self.big_roads.toggled.connect(lambda v:self.big_setting('show_roads',v));group.addWidget(self.big_roads)
        self.coordinate_enabled=QCheckBox('Считывать координаты перекрестия, если рельеф не распознан')
        self.coordinate_enabled.setChecked(self.settings['bigmap']['coordinates'])
        self.coordinate_enabled.toggled.connect(lambda v:self.big_setting('coordinates',v));group.addWidget(self.coordinate_enabled)
        group.addWidget(self.text('Область должна включать перекрестие на карте и надписи x/y. В режиме координат можно задавать цели; наложение маршрута ждёт восстановления распознавания рельефа. Держите перекрестие неподвижно и после нажатия клавиши не сдвигайте его до подтверждения.',True))
        form=QFormLayout();self.big_fields={}
        for key,label,lo,hi in [('left','Левый край X',-30000,30000),('top','Верхний край Y',-30000,30000),('width','Ширина',60,5000),('height','Высота',60,5000)]:
            field=number(self.settings['bigmap_capture'][key],lo,hi)
            field.valueChanged.connect(lambda v,k=key:self.big_capture_setting(k,v));self.big_fields[key]=field;form.addRow(label,field)
        for key,label,lo,hi,dec in [('opacity','Непрозрачность наложения',.2,1,2),('line_width','Толщина линии маршрута (пиксели экрана)',1,10,0),('avoid_radius_m','Радиус опасной зоны по клавише (м)',5,500,0)]:
            field=number(self.settings['bigmap'][key],lo,hi,dec)
            field.valueChanged.connect(lambda v,k=key:self.big_setting(k,v));form.addRow(label,field)
        group.addLayout(form)
        self.big_status=self.text('Большая карта ещё не распознана',True);group.addWidget(self.big_status)
        form=QFormLayout();self.hotkey_fields={}
        for action,label in hotkeys.ACTIONS.items():
            field=QKeySequenceEdit(QKeySequence(self.settings['bigmap_hotkeys'][action]))
            field.setMaximumSequenceLength(1);self.hotkey_fields[action]=field;form.addRow(label,field)
        group.addLayout(form);group.addWidget(self.button('Сохранить горячие клавиши',self.save_map_hotkeys))
        self.hotkey_status=self.text('Работают, когда распознана большая карта игры в активном окне; пустое поле отключает действие. Поддерживаются Ctrl / Alt с буквами, цифрами, [, ] или F1–F11; * — умножение на цифровом блоке или Shift+8, можно без модификаторов.',True);group.addWidget(self.hotkey_status)
        group.addWidget(self.text('Новая навигация: последнее положение по мини-карте становится стартом, точка под мышью — целью, старые промежуточные точки удаляются. Последнее положение машины хранится до следующего определения или смены карты; если положения ещё нет, старт подставится после возврата к мини-карте. При смене цели старт сохраняется.',True))

    def save_map_hotkeys(self):
        bindings={key:field.keySequence().toString(QKeySequence.PortableText) for key,field in self.hotkey_fields.items()}
        used=set()
        try:
            for action,text in bindings.items():
                for binding in hotkeys.hotkey_bindings(text):
                    if binding in used:raise ValueError('Клавиша повторяется')
                    used.add(binding)
        except ValueError as error:
            message=hotkeys.ACTIONS[action]+': '+str(error)
            self.hotkey_status.setText(message);self.notify('Горячие клавиши не сохранены: '+message);return
        self.settings['bigmap_hotkeys']=bindings;self.global_hotkeys.configure(bindings);self.save_settings()
        if not self.global_hotkeys.active:self.hotkey_status.setText('Горячие клавиши сохранены; заработают, когда будет распознана большая карта в активном окне')

    def hotkey_status_changed(self,message):
        self.hotkey_status.setText(message)
        if any(word in message.lower() for word in ('занят','не удалось')):self.map_feedback(message)

    def big_setting(self,key,value):
        self.settings['bigmap'][key]=value
        if key in ('enabled','coordinates'):self.reset_game_map();self.worker.reset_view()
        self.save_settings();self.update_big_overlay()

    def big_capture_setting(self,key,value):
        self.settings['bigmap_capture'][key]=value
        self.reset_game_map();self.worker.reset_view();self.save_settings()

    def reset_game_map(self):
        self.hide_game_map();self.game_window=0

    def hide_game_map(self):
        self.big_view=None;self.coordinate_view=None;self.big_tracking=False;self.cursor_timer.stop();self.global_hotkeys.set_active(False);self.big_overlay.hide()

    def big_view_available(self):
        return (self.worker.capture and self.settings['bigmap']['enabled'] and self.big_view is not None
            and self.big_view.valid and self.big_view.session==self.worker.session
            and (self.big_view.map_id,self.big_view.generation)==self.worker.context
            and self.big_view.region==self.settings['bigmap_capture'])

    def big_view_fresh(self):
        return self.big_view_available() and self.big_tracking and time.monotonic()-self.big_view.captured_at<2.5

    def coordinate_available(self):
        view=self.coordinate_view
        return (self.worker.capture and self.settings['bigmap']['enabled'] and self.settings['bigmap']['coordinates']
            and view is not None and view.coordinate is not None and view.session==self.worker.session
            and (view.map_id,view.generation)==self.worker.context and view.region==self.settings['bigmap_capture']
            and time.monotonic()-view.captured_at<2.5)

    def game_view_available(self):
        return self.big_view_available() or self.coordinate_available()

    def game_map_active(self):
        return (self.game_view_available() and self.worker.mode!='mini' and self.game_window
                and hotkeys.foreground_window()==self.game_window)

    def big_localization(self):
        if self.coordinate_available():return 'Координаты большой карты · '+format_coordinates(self.coordinate_view.coordinate.game)
        return f'Распознавание большой карты · {self.big_view.confidence:.0%}' if self.big_view_available() else 'Большая карта ещё не распознана'

    def on_big_map(self,view,frame,actions=None):
        if (not self.worker.capture or not self.settings['bigmap']['enabled'] or view.session!=self.worker.session
            or (view.map_id,view.generation)!=self.worker.context or view.region!=self.settings['bigmap_capture']):return
        self.big_status.setText(view.reason)
        if self.worker.mode=='mini':return
        if not view.foreground or hotkeys.foreground_window()!=view.foreground:
            self.hide_game_map();return
        if self.game_window and view.foreground!=self.game_window:
            self.hide_game_map();return
        if not (view.valid or view.coordinate) or time.monotonic()-view.captured_at>=2.5:
            self.big_tracking=False
            if actions:self.worker.retry_actions(actions,view.foreground)
            self.invalidate_fix(view.reason);self.update_big_overlay();return
        self.game_window=view.foreground;self.worker.target_window=view.foreground
        self.big_view=view if view.valid else None
        self.coordinate_view=view if not view.valid and view.coordinate else None
        if self.coordinate_view:self.big_overlay.hide()
        self.big_tracking=True
        self.fix_live=False;self.minimap_overlay.hide();self.navigator.lost();self.tts.stop()
        self.disconnect_recording()
        self.set_localization(self.big_localization())
        self.set_hud({'state':'bigmap','text':'Работа с большой картой'})
        self.big_status.setText(f'{view.reason} · {count(view.inliers,"совпадение","совпадения","совпадений")}' if view.valid else self.big_localization()+' · горячие клавиши доступны')
        self.global_hotkeys.set_active(True)
        self.cursor_timer.start()
        self.update_big_overlay()
        if actions:
            for action in actions:
                if action[0]!=view.session or action[3]!=view.foreground or time.monotonic()-action[4]>=6:
                    self.map_feedback('Время действия истекло или окно сменилось, повторите');continue
                if view.valid:point=self.valid_map_point(view.screen_to_map(action[2]))
                elif view.coordinate.matches(action[2],view.region):point=self.valid_map_point(view.coordinate.point)
                else:
                    self.worker.retry_actions([action],view.foreground)
                    self.map_feedback('Не сдвигайте перекрестие, ждите подтверждения координат');continue
                if point is not None:self.apply_map_action(action[1],point)
                else:self.map_feedback('Мышь вне допустимой области карты')

    def valid_map_point(self,point):
        if point is None:return None
        info=map_info(self.project['map'])
        return list(point) if 0<=point[0]<=info['width'] and 0<=point[1]<=info['height'] else None

    def poll_map_cursor(self):
        if not self.game_map_active():
            self.hide_game_map();return
        if self.coordinate_available():
            self.big_status.setText(self.big_localization()+' · горячие клавиши доступны');return
        if not self.big_view_fresh():
            self.big_status.setText('Ждём восстановления распознавания большой карты; клавиша сработает после проверки нового кадра');return
        point=hotkeys.physical_cursor()
        mapped=self.valid_map_point(self.big_view.screen_to_map(point)) if point else None
        if mapped:self.big_status.setText('Мышь на большой карте '+format_coordinates(map_to_game(self.project['map'],mapped))+' · горячие клавиши доступны')
        else:self.big_status.setText('Мышь вне области большой карты')

    def on_map_hotkey(self,action):
        # This guard precedes the ONLY cursor query in the hotkey path.
        if action not in hotkeys.ACTIONS:return
        self.hotkey_status.setText('Нажата горячая клавиша: '+hotkeys.ACTIONS[action])
        if not self.game_map_active():
            self.hotkey_status.setText('Горячая клавиша нажата, но в активном окне нет большой карты игры');return
        point=hotkeys.physical_cursor()
        region=self.settings['bigmap_capture']
        if point is None or not (region['left']<=point[0]<region['left']+region['width'] and region['top']<=point[1]<region['top']+region['height']):
            self.map_feedback('Мышь вне выделенной области большой карты');return
        if self.worker.request_action(action,point,self.game_window):self.map_feedback('Проверяем положение мыши…')

    def map_feedback(self,text):
        self.big_message=text;self.big_message_until=time.monotonic()+2
        self.big_status.setText(text)
        self.hotkey_status.setText(text)
        self.update_big_overlay()

    def update_big_overlay(self):
        if not self.big_view_available():return
        text=self.big_message if time.monotonic()<self.big_message_until else 'Большая карта · горячие клавиши готовы · подсказки в пути приостановлены'
        self.big_overlay.update_map(self.big_view,self.project,self.route,self.full_route,
            self.navigator.progress if self.navigator.active else 0,
            self.last_accepted,text)

    def resume_context(self):
        if self.big_resume:return self.big_resume
        if not self.navigator.active:return None
        passed=[]
        for point in self.project['waypoints']:
            hit=nearest_on_route(point,self.navigator.route.points)
            if hit and hit[0]<=25 and hit[1]<=self.navigator.progress+3:passed.append(point)
        return dict(lap=self.navigator.lap,leg=self.navigator.leg,passed=passed)

    def apply_map_action(self,action,point):
        if action=='clear':
            self.clear_current_route();self.map_feedback('Маршрут и старт удалены');return
        resume=self.resume_context();pending=self.pending_start
        if action=='navigate':
            self.snapshot();self.detach_favorite();self.stop_navigation(quiet=True)
            self.navigator.lap=0;self.navigator.leg='путь туда'
            self.project['start']=list(self.last_accepted) if self.last_accepted is not None else None
            self.project['destination']=point;self.project['waypoints']=[]
            self.changed();self.pending_start=True
            message='Новый маршрут построен; подсказки начнутся после закрытия большой карты' if self.route else 'Цель задана; после возврата к мини-карте определим положение и построим маршрут'
            if self.project['start'] and not self.route:message='Проезжий маршрут не найден; измените цель или правила дорог'
            self.flash_hud('Цель задана')
            self.map_feedback(message);self.hud.show();return
        if action=='undo':
            if not self.history:self.map_feedback('Нечего отменять');return
            self.undo()
        else:
            self.snapshot()
            if action=='start':self.project['start']=point;self.favorite_trip=None
            elif action=='destination':self.detach_favorite();self.project['destination']=point
            elif action=='waypoint':self.detach_favorite();self.project['waypoints'].append(point)
            elif action=='avoid':
                radius=self.settings['bigmap']['avoid_radius_m']/(self.project['meters_per_pixel'] or 1)
                self.project['avoid'].append(dict(point=point,radius=max(1,min(1000,radius)),shape='circle'))
            else:return
            self.changed()
        self.big_resume=resume if self.project.get('destination') and self.project.get('start') else None
        self.pending_start=pending and bool(self.project.get('destination'))
        if self.big_resume and self.last_accepted:self.plan_big_resume(self.last_accepted,start=False)
        self.map_feedback(hotkeys.ACTIONS[action]+': готово'+(' · проезжего маршрута нет, измените отметки' if not self.route and self.project.get('destination') and self.project.get('start') else ''))

    def plan_big_resume(self,position,start=True):
        resume=self.big_resume
        if not resume:return
        waypoints=[p for p in self.project['waypoints'] if p not in resume['passed']]
        if resume['lap']%2:waypoints.reverse()
        goal=self.project['start'] if resume['lap']%2 else self.project['destination']
        self.navigator.lap=resume['lap']
        success=bool(goal) and self.plan_route(quiet=True,anchors=[list(position),*waypoints,goal])
        if start:
            self.big_resume=None
            if success:
                self.start_trip_navigation(resume['lap'],resume['leg'])
            else:self.set_hud({'state':'offroute','text':'Нет проезжего маршрута'})
