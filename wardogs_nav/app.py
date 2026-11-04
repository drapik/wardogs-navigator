from __future__ import annotations
from copy import deepcopy
from dataclasses import asdict,replace
from pathlib import Path
import json
import math
import time
import numpy as np
from PySide6.QtCore import Qt, QTimer, QLocale
from PySide6.QtGui import QImage, QPixmap, QFont, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QLabel,
    QPushButton,QTabWidget,QScrollArea,QFormLayout,QCheckBox,QComboBox,QLineEdit,QListWidget,
    QGroupBox,QFileDialog,QMessageBox,QInputDialog,QDialog,QSplitter,QFrame)
from wardogs_audio.navigation import NavigationSpeech
from wardogs_audio.speechkit import configured as speechkit_configured, load_config as load_speechkit_config
from .model import (asset_path,user_dir,atomic_json,default_settings,load_settings,read_project,
                    validate_project,upgrade_road_data,uid,KINDS,NOTE_TYPES,ROAD_PREFERENCES)
from .vision import Fix
from . import __version__
from .routing import plan,RouteError,distance,bearing,nearest_on_route,distance_to_roads,bridge_ports,roads_connect
from .navigation import Navigator, cues_for, cue_text, distance_text
from .library import saved_route,supplement_roads,follow_saved,library_payload,merge_library,snap_saved,check_library_target
from .maps import all_maps,map_info,map_asset,project_path,load_map_project
from .mapview import MapView
from .hud import Hud
from .dialogs import RoadDialog,NoteDialog,RegionDialog,FavoriteImportDialog,number
from .worker import CaptureWorker
from .overlay import MinimapOverlay,keep_on_top
from .game_map import GameMapController
from .widgets import protect_settings_wheel
from .recording_ui import RecordingController
from .coordinates import format_coordinates,map_to_game
from .ru import plural

STYLE='''
QWidget { background:#151d25; color:#e5ecee; font:10pt "Microsoft YaHei UI"; }
QMainWindow { background:#10171e; }
QLabel { background:transparent; }
QLabel#title { font:700 21pt "Segoe UI"; letter-spacing:2px; }
QLabel#muted { color:#93a6af; font-size:9pt; }
QLabel#banner { background:#22352f; color:#bfead9; padding:10px; border:1px solid #365148; border-radius:7px; }
QPushButton { background:#25313d; border:1px solid #3a4b58; border-radius:6px; padding:7px 10px; }
QPushButton:hover { background:#344958; border-color:#78938f; }
QPushButton:pressed { background:#1b2d35; }
QPushButton:disabled { color:#64757d; border-color:#2b3740; }
QPushButton#primary { background:#62ddba; color:#10281f; font-weight:700; border:0; padding:10px; }
QPushButton#primary:hover { background:#8ef0d3; }
QPushButton#selected { background:#456959; }
QTabWidget::pane { border:0; }
QTabBar::tab { background:#151d25; color:#96a8b1; padding:10px 18px; border-bottom:2px solid #253440; }
QTabBar::tab:selected { color:#77e5c3; border-bottom:2px solid #77e5c3; }
QLineEdit,QComboBox,QSpinBox,QDoubleSpinBox { background:#10181f; border:1px solid #344550; border-radius:5px; padding:5px; min-height:22px; }
QComboBox::drop-down { border:0; width:22px; }
QCheckBox { spacing:8px; padding:4px 0; }
QCheckBox::indicator { width:16px; height:16px; border:1px solid #5b7380; border-radius:3px; background:#10181f; }
QCheckBox::indicator:checked { background:#61daba; border-color:#61daba; }
QGroupBox { border:1px solid #30404b; border-radius:7px; margin-top:16px; padding:14px 10px 10px; font-weight:600; }
QGroupBox::title { subcontrol-origin:margin; left:10px; padding:0 5px; color:#b4c6cf; }
QListWidget { background:#111a21; border:1px solid #30404b; border-radius:5px; padding:4px; }
QListWidget::item { padding:6px 4px; }
QListWidget::item:selected { background:#29483f; color:#96efd1; }
QScrollArea { border:0; }
QScrollBar:vertical { background:#16212a; width:9px; }
QScrollBar::handle:vertical { background:#41545e; border-radius:4px; min-height:25px; }
QScrollBar::add-line:vertical,QScrollBar::sub-line:vertical { height:0; }
QToolTip { color:#e5ecee; background:#293943; border:1px solid #607b88; }
QStatusBar { background:#11191f; color:#9bb1bd; }
'''



def speechkit_status():
    if not speechkit_configured():
        return 'Ключ Yandex SpeechKit не найден: задайте YANDEX_SPEECHKIT_API_KEY или speechkit.json (см. README)'
    config=load_speechkit_config()
    return f"Yandex SpeechKit · голос {config['voice']}"+(f" ({config['role']})" if config.get('role') else '')+' · фразы кэшируются'

class MainWindow(QMainWindow,GameMapController,RecordingController):
    def __init__(self, start_worker=True):
        super().__init__()
        protect_settings_wheel()
        screen=QApplication.primaryScreen().availableGeometry()
        self.resize(min(1400,int(screen.width()*.94)),min(900,int(screen.height()*.92)))
        self.setMinimumSize(1000,660)
        self.settings=load_settings()
        if self.settings['map_id'] not in {m['id'] for m in all_maps()}:self.settings['map_id']='ozeti'
        self.project=read_project(map_asset(self.settings['map_id'],'project'))
        self.load_error=None
        if project_path(self.settings['map_id']).exists():
            try:self.project=load_map_project(self.settings['map_id'])
            except (OSError,ValueError,KeyError,TypeError) as error:self.load_error=str(error)
        upgraded,changed=upgrade_road_data(self.project)
        if changed:
            backup=user_dir()/'project-before-map-v2.json'
            if not backup.exists():atomic_json(backup,self.project)
            self.project=upgraded
        self.history=[]
        self.route=None
        self.full_route=None
        self.fix=None
        self.fix_live=False
        self.fix_time=0.
        self.pending_start=False
        self.jump_candidate=None
        self.jump_count=0
        self.last_accepted=None
        self.last_accepted_time=0.
        self.frame=None
        self.frame_diagnostic=None
        self.failure_diagnostic=None
        self.favorite_trip=None
        self.draft_kinds=[]
        self.navigator=Navigator()
        self.init_recording()
        self.calibration_points=[]
        self.worker=CaptureWorker(self.settings)
        self.worker.result.connect(self.on_fix)
        self.worker.ready.connect(self.worker_ready)
        self.worker.failed.connect(self.worker_error)
        self.tts=NavigationSpeech(self.settings,self)
        self.tts.status.connect(lambda message:self.voice_status.setText(message))
        self.tts.voices_ready.connect(self.voices_loaded)
        self.tts.completed.connect(lambda receipt:self.speech_result(receipt,True))
        self.tts.cancelled.connect(lambda receipt:self.speech_result(receipt,False))
        self.voices=[]
        self.hud=Hud(self.settings['hud'])
        self.hud_navigation=None;self.hud_problem=None;self.hud_notice=None
        self.hud_notice_timer=QTimer(self);self.hud_notice_timer.setSingleShot(True)
        self.hud_notice_timer.setInterval(2000);self.hud_notice_timer.timeout.connect(self.expire_hud_notice)
        self.minimap_overlay=MinimapOverlay(self.settings['minimap_overlay'])
        self.minimap_overlay.mask_changed.connect(lambda mask:setattr(self.worker,'path_mask',mask))
        self.init_game_map()
        self.hud.changed.connect(self.save_settings)
        self.build_ui()
        self.setWindowTitle('Навигатор WARDOGS '+__version__+' · '+map_info(self.project['map'])['name'])
        self.setWindowFlag(Qt.WindowStaysOnTopHint,self.settings['main_topmost'])
        self.refresh_lists()
        self.refresh_map()
        self.autosave=QTimer(self);self.autosave.setSingleShot(True);self.autosave.setInterval(500);self.autosave.timeout.connect(self.save_project)
        self.watchdog=QTimer(self);self.watchdog.timeout.connect(self.check_stale);self.watchdog.start(500)
        QTimer.singleShot(700,self.load_voices)
        if start_worker:self.worker.start()
        QTimer.singleShot(0,self.map.fit)
        if self.load_error:QTimer.singleShot(300,lambda:self.notify('Не удалось прочитать прошлую конфигурацию, загружена встроенная дорожная сеть; исходный файл сохранён. '+self.load_error))
        elif changed:QTimer.singleShot(300,lambda:self.notify('Подложка карты и дороги обновлены; цели, опасные зоны и ваши правки сохранены, старая конфигурация в резервной копии.'))
        elif self.project['map']=='ozeti' and self.project.get('road_data_revision',1)<2:QTimer.singleShot(300,lambda:self.notify('Ваши правки дорог сохранены; новые данные дорог можно загрузить во вкладке «Разметка карты».'))
        QShortcut(QKeySequence('Ctrl+Z'),self,self.undo)
        QShortcut(QKeySequence('Escape'),self,self.cancel_tool)
        QShortcut(QKeySequence('Return'),self,self.finish_road)
        QShortcut(QKeySequence('Ctrl+S'),self,self.save_project)
        QShortcut(QKeySequence('Backspace'),self,self.undo_draw_point)

    def button(self,text,callback,primary=False):
        b=QPushButton(text);b.clicked.connect(callback)
        if primary:b.setObjectName('primary')
        return b

    def row(self,*widgets):
        box=QWidget();layout=QHBoxLayout(box);layout.setContentsMargins(0,0,0,0);layout.setSpacing(6)
        for widget in widgets:layout.addWidget(widget)
        return box

    def text(self,text,muted=False):
        label=QLabel(text);label.setWordWrap(True)
        if muted:label.setObjectName('muted')
        return label

    def group(self,title,parent):
        box=QGroupBox(title);layout=QVBoxLayout(box);layout.setSpacing(8);parent.addWidget(box);return layout

    def page(self,title):
        widget=QWidget();layout=QVBoxLayout(widget);layout.setContentsMargins(6,9,10,12);layout.setSpacing(8)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setWidget(widget);self.tabs.addTab(scroll,title);return layout

    def build_ui(self):
        self.clear_waypoint_buttons=[];self.clear_avoid_buttons=[]
        central=QWidget();self.setCentralWidget(central);outer=QVBoxLayout(central);outer.setContentsMargins(18,14,18,10);outer.setSpacing(12)
        header=QHBoxLayout();title=QLabel('WARDOGS');title.setObjectName('title');header.addWidget(title)
        header.addWidget(self.text('Навигация наземной техники',True))
        self.map_combo=QComboBox();self.map_combo.setMinimumWidth(155)
        for info in all_maps():self.map_combo.addItem(info['name'],info['id'])
        self.map_combo.setCurrentIndex(self.map_combo.findData(self.project['map']))
        self.map_combo.setToolTip('Сменить карту; дороги, маршруты, стенограммы и опасные зоны хранятся отдельно для каждой карты')
        self.map_combo.currentIndexChanged.connect(self.choose_map);header.addWidget(self.map_combo);header.addStretch()
        for label,callback in [('Отменить',self.undo),('Импорт настроек',self.import_project),('Экспорт настроек',self.export_project),('Справка',self.help)]:header.addWidget(self.button(label,callback))
        outer.addLayout(header)
        self.banner=self.text('① Выделите миникарту и включите позиционирование   ② Укажите цель и путевые точки   ③ Начните навигацию');self.banner.setObjectName('banner');outer.addWidget(self.banner)
        splitter=QSplitter();outer.addWidget(splitter,1)
        self.tabs=QTabWidget();self.tabs.setMinimumWidth(350);self.tabs.setMaximumWidth(475);splitter.addWidget(self.tabs)
        right=QWidget();right_layout=QVBoxLayout(right);right_layout.setContentsMargins(8,0,0,0)
        toolbar_box=QWidget();toolbar_box.setStyleSheet('QPushButton { padding:7px 4px; }')
        toolbar_layout=QVBoxLayout(toolbar_box);toolbar_layout.setContentsMargins(0,0,0,0);toolbar_layout.setSpacing(6)
        toolbar=QHBoxLayout();toolbar_layout.addLayout(toolbar)
        for label,tool in [('Обзор / выбор','pan'),('Старт','start'),('Цель','destination'),('Опасная зона','avoid'),('Путевая точка','waypoint'),('Рисовать дорогу','road')]:toolbar.addWidget(self.button(label,lambda _,t=tool:self.set_tool(t)))
        toolbar.addStretch();toolbar.addWidget(self.button('Вся карта',lambda:self.map.fit()));right_layout.addWidget(toolbar_box)
        cleanup=QHBoxLayout();toolbar_layout.addLayout(cleanup)
        cleanup.addWidget(self.clear_route_button('toolbar'))
        cleanup.addWidget(self.clear_button('waypoints','toolbar'));cleanup.addWidget(self.clear_button('avoid','toolbar'));cleanup.addStretch()
        self.draw_kind=QComboBox()
        for key,label in KINDS.items():self.draw_kind.addItem(label,key)
        self.draw_kind.setCurrentIndex(1)
        self.draw_snap=QCheckBox('Привязка к дорогам');self.draw_snap.setChecked(True)
        self.draw_kind_label=QLabel('Дорога / тип следующего участка')
        self.drawing_bar=self.row(self.draw_kind_label,self.draw_kind,self.draw_snap,
                                  self.button('Шаг назад',self.undo_draw_point),self.button('Готово',self.finish_road),self.button('Отмена',self.cancel_tool))
        self.drawing_bar.hide();right_layout.addWidget(self.drawing_bar)
        self.favorite_snap=QCheckBox('Привязать к дорогам')
        self.favorite_build=QCheckBox('Достроить недостающие дороги');self.favorite_build.setChecked(True)
        self.favorite_options=self.row(self.favorite_snap,self.favorite_build)
        self.favorite_options.hide();right_layout.addWidget(self.favorite_options)
        self.favorite_preview_label=self.text('',True);self.favorite_preview_label.hide();right_layout.addWidget(self.favorite_preview_label)
        self.favorite_snap.toggled.connect(self.favorite_drawing_option)
        self.favorite_build.toggled.connect(self.favorite_drawing_option)
        self.draft_preview=None
        self.map=MapView(self.project['map']);right_layout.addWidget(self.map,1)
        self.map.clicked.connect(self.map_click);self.map.finished.connect(self.finish_road);self.map.moved.connect(self.move_item);self.map.remove_requested.connect(self.remove_item)
        self.map.region_selected.connect(self.add_danger_region)
        self.map.road_selected.connect(self.select_road_on_map)
        self.map.road_edit_requested.connect(self.edit_road_on_map)
        self.map.road_delete_requested.connect(self.delete_road_on_map)
        self.map.hover.connect(self.show_map_coordinates)
        bottom=QHBoxLayout();legend=self.text('┄ Главная   ┄ Второстепенная   ┄ Бездорожье   ━ Маршрут',True);legend.setWordWrap(False);bottom.addWidget(legend);bottom.addStretch()
        self.coordinate_label=self.text('Игровые координаты —',True);self.coordinate_label.setWordWrap(False);bottom.addWidget(self.coordinate_label);right_layout.addLayout(bottom)
        splitter.addWidget(right);splitter.setSizes([390,1050]);splitter.setStretchFactor(1,1)
        self.build_navigation();self.build_editor();self.build_library();self.build_settings()
        self.statusBar().showMessage('Запуск визуального позиционирования…')

    def build_navigation(self):
        layout=self.page('Навигация')
        status=self.group('Позиционирование',layout)
        self.fix_label=self.text('Позиция не определена');self.fix_label.setStyleSheet('font-size:15pt;color:#75e5c3;');status.addWidget(self.fix_label)
        self.fix_detail=self.text('Миникарта: север всегда сверху; проверьте превью захвата в настройках.',True);status.addWidget(self.fix_detail)
        self.capture_button=self.button('Включить позиционирование',self.toggle_capture)
        status.addWidget(self.row(self.button('Выделить миникарту',self.select_region),self.capture_button))
        self.start_button=self.button('Начать навигацию',self.start_navigation,True);status.addWidget(self.start_button)
        status.addWidget(self.row(self.button('Стоп',self.stop_navigation),self.button('Показать значок',self.show_hud),self.button('Скрыть значок',self.hud.hide)))
        target=self.group('Старт, цель и путевые точки',layout)
        target.addWidget(self.clear_route_button('navigation'))
        target.addWidget(self.row(self.clear_button('waypoints','navigation'),self.clear_button('avoid','navigation')))
        self.start_label=self.text('');target.addWidget(self.start_label)
        self.set_start_button=self.button('Старт на карте',lambda:self.set_tool('start'))
        self.current_start_button=self.button('Текущая позиция',self.use_current_start)
        target.addWidget(self.row(self.set_start_button,self.current_start_button))
        target.addWidget(self.text('Старт — это и конец обратного пути, его можно менять в дороге; если не задан, при запуске навигации берётся текущая позиция.',True))
        self.destination_label=self.text('Укажите цель на карте');target.addWidget(self.destination_label)
        self.saved_dest=QComboBox();self.saved_dest.currentIndexChanged.connect(self.choose_destination);target.addWidget(self.saved_dest)
        target.addWidget(self.row(self.button('Выбрать на карте',lambda:self.set_tool('destination')),self.button('Сохранить цель',self.save_destination)))
        self.waypoint_list=QListWidget();self.waypoint_list.setMaximumHeight(90);target.addWidget(self.waypoint_list)
        target.addWidget(self.row(self.button('Добавить точку',lambda:self.set_tool('waypoint')),self.button('Удалить выбранную',self.delete_waypoint),self.button('Выделить опасную зону',lambda:self.set_tool('avoid'))))
        target.addWidget(self.text('Выделите опасную зону рамкой — маршрут её обойдёт; зелёные точки задают обязательные места проезда. ПКМ удаляет метку.',True))
        self.roundtrip=QCheckBox('Челнок (по прибытии автоматически переключать туда / обратно)');self.roundtrip.setChecked(self.project['roundtrip']);self.roundtrip.toggled.connect(self.change_policy);target.addWidget(self.roundtrip)
        arrival=QFormLayout();self.arrival_radius=number(self.settings['arrival_m'],5,1000)
        self.arrival_radius.valueChanged.connect(lambda v:self.set_setting('arrival_m',v));arrival.addRow('Радиус прибытия (м)',self.arrival_radius);target.addLayout(arrival)
        target.addWidget(self.text('Прибытие засчитывается после входа в радиус цели и нескольких подтверждений позиции, в обе стороны. Без калибровки масштаба считается в единицах карты.',True))
        self.build_coordinate_input(layout)
        rules=self.group('Предпочтения дорог',layout);self.kind_preferences={};rf=QFormLayout()
        for key,label in KINDS.items():
            choice=QComboBox()
            for value,name in ROAD_PREFERENCES.items():choice.addItem(name,value)
            choice.setCurrentIndex(choice.findData(self.project['policy']['preferences'][key]))
            choice.currentIndexChanged.connect(self.change_policy);self.kind_preferences[key]=choice;rf.addRow(label,choice)
        rules.addLayout(rf)
        rules.addWidget(self.text('Предпочтительные дороги выбираются первыми, другие типы используются для связок при необходимости; «избегать» не разрывает сеть. Опасные зоны закрыты всегда.',True))
        controls=self.group('Навигация и подсказки',layout)
        self.mode=QComboBox();self.mode.addItem('Обычная навигация','normal');self.mode.addItem('Стенограмма WRC','wrc');self.mode.setCurrentIndex(self.mode.findData(self.settings['mode']));self.mode.currentIndexChanged.connect(self.change_mode);controls.addWidget(self.mode)
        self.route_label=self.text('Маршрут ещё не построен',True);controls.addWidget(self.route_label)
        controls.addWidget(self.button('Показать маршрут',self.preview_route))
        controls.addWidget(self.row(self.button('Сохранить маршрут',self.save_current_route),self.button('Список подсказок',self.preview_cues)))
        layout.addStretch()

    def build_editor(self):
        layout=self.page('Разметка карты')
        roads=self.group('Дороги',layout)
        self.roads_visible=QCheckBox('Показывать дороги');self.roads_visible.setChecked(True);self.roads_visible.toggled.connect(self.toggle_roads);roads.addWidget(self.roads_visible)
        self.road_list=QListWidget();self.road_list.setMinimumHeight(180);self.road_list.currentRowChanged.connect(self.select_road);self.road_list.itemDoubleClicked.connect(lambda _:self.edit_road());roads.addWidget(self.road_list)
        roads.addWidget(self.row(self.button('Рисовать дорогу',lambda:self.set_tool('road')),self.button('Завершить',self.finish_road)))
        roads.addWidget(self.row(self.button('Свойства',self.edit_road),self.button('Удалить дорогу',self.delete_road)))
        roads.addWidget(self.row(self.button('Импорт дорог',lambda:self.import_library('roads')),self.button('Экспорт дорог',lambda:self.export_library('roads'))))
        roads.addWidget(self.button('Загрузить встроенные дороги',self.reload_roads))
        roads.addWidget(self.text('В режиме «Обзор / выбор» щелчок выбирает дорогу, точки можно тянуть, двойной щелчок — свойства, ПКМ — правка или удаление; дорогу можно выбрать и в списке. При рисовании Enter / двойной щелчок завершают, на пересечениях нужны явные узлы.',True))
        self.build_recording(layout)
        notes=self.group('Стенограмма WRC',layout)
        self.note_list=QListWidget();self.note_list.setMaximumHeight(140);self.note_list.itemDoubleClicked.connect(lambda _:self.edit_note());notes.addWidget(self.note_list)
        notes.addWidget(self.row(self.button('Добавить на карте',lambda:self.set_tool('note')),self.button('Изменить',self.edit_note),self.button('Удалить',self.delete_note)))
        notes.addWidget(self.text('Ручные метки важнее расчётной сложности поворотов. Экспорт настроек включает дороги, стенограмму, зоны объезда, цели и правила маршрута.',True))
        avoids=self.group('Зоны объезда',layout)
        self.avoid_list=QListWidget();self.avoid_list.setMaximumHeight(100);avoids.addWidget(self.avoid_list)
        avoids.addWidget(self.row(self.button('Выделить опасную зону',lambda:self.set_tool('avoid')),self.button('Размер',self.edit_avoid),self.button('Удалить',self.delete_avoid)))
        avoids.addWidget(self.text('Тяните центр зоны, чтобы переместить, правый нижний угол — чтобы изменить размер; дороги в красной зоне закрыты.',True))
        layout.addStretch()

    def build_library(self):
        layout=self.page('Сохранённые маршруты')
        layout.addWidget(self.text('Сохранение полного маршрута',False))
        layout.addWidget(self.text('Сохраните готовый маршрут или нарисуйте свой: с «Привязать к дорогам» достаточно грубо расставить точки — маршрут пройдёт по дорогам. Без привязки путь сохраняется как нарисован, а общие дороги можно достроить по желанию. Опасные зоны объезжаются локально.',True))
        self.favorite_list=QListWidget();self.favorite_list.setMinimumHeight(180)
        self.favorite_list.itemDoubleClicked.connect(lambda _:self.load_favorite());layout.addWidget(self.favorite_list)
        self.favorite_status=self.text('Сейчас — автопостроение',True);layout.addWidget(self.favorite_status)
        layout.addWidget(self.row(self.button('Загрузить',self.load_favorite),self.button('Загрузить в обратную сторону',lambda:self.load_favorite(reverse=True))))
        layout.addWidget(self.row(self.button('Сохранить маршрут',self.save_current_route),self.button('Нарисовать маршрут',lambda:self.set_tool('favorite'))))
        layout.addWidget(self.row(self.button('Переименовать',self.rename_favorite),self.button('Удалить маршрут',self.delete_favorite)))
        layout.addWidget(self.row(self.button('Импорт маршрутов',lambda:self.import_library('routes')),self.button('Экспорт маршрутов',lambda:self.export_library('routes'))))
        layout.addWidget(self.button('Выйти из маршрута, вернуть автопостроение',self.clear_favorite))
        layout.addWidget(self.text('Без привязки можно менять тип следующего участка. Маршрут без достройки дорог тоже ведёт навигацию, недостающие участки используются только в нём. При импорте подтверждаются опции привязки и достройки; экспорт сохраняет путь, типы, опорные точки, опции и стенограмму. Кнопки настроек вверху сохраняют всё целиком.',True))
        layout.addStretch()

    def build_settings(self):
        layout=self.page('Настройки')
        capture=self.group('Область захвата экрана',layout)
        self.screen_combo=QComboBox();self.monitors=[]
        try:
            import mss
            with mss.MSS() as sct:self.monitors=[dict(m) for m in sct.monitors[1:]]
        except Exception:pass
        for i,m in enumerate(self.monitors):self.screen_combo.addItem(f"Монитор {i+1} · {m['width']} × {m['height']}")
        capture.addWidget(self.screen_combo)
        capture.addWidget(self.button('Выделить миникарту на этом экране',self.select_region))
        form=QFormLayout();self.capture_fields={}
        for key,label,lo,hi in [('left','Слева X',-30000,30000),('top','Сверху Y',-30000,30000),('width','Ширина',60,5000),('height','Высота',60,5000)]:
            w=number(self.settings['capture'][key],lo,hi);w.valueChanged.connect(lambda value,k=key:self.capture_setting(k,value));self.capture_fields[key]=w;form.addRow(label,w)
        self.anchor_x=number(self.settings['anchor'][0]*100,0,100,1);self.anchor_y=number(self.settings['anchor'][1]*100,0,100,1)
        self.anchor_x.valueChanged.connect(self.anchor_setting);self.anchor_y.valueChanged.connect(self.anchor_setting)
        form.addRow('Метка игрока X (%)',self.anchor_x);form.addRow('Метка игрока Y (%)',self.anchor_y)
        self.interval=number(self.settings['interval_ms'],200,3000);self.interval.valueChanged.connect(lambda v:self.set_setting('interval_ms',v));form.addRow('Интервал распознавания (мс)',self.interval);capture.addLayout(form)
        self.north_up=QCheckBox('Север миникарты всегда сверху');self.north_up.setChecked(self.settings['north_up']);self.north_up.toggled.connect(lambda v:self.set_setting('north_up',v));capture.addWidget(self.north_up)
        self.preview=QLabel('Превью захвата появится при позиционировании');self.preview.setAlignment(Qt.AlignCenter);self.preview.setMinimumHeight(140);capture.addWidget(self.preview)
        capture.addWidget(self.button('Проверить по картинке',self.test_image))
        capture.addWidget(self.button('Сохранить диагностику',self.save_capture_diagnostic))
        overlay=self.group('Поверх окон и путь на миникарте',layout)
        topmost=QCheckBox('Главное окно поверх остальных');topmost.setChecked(self.settings['main_topmost']);topmost.toggled.connect(self.set_main_topmost);overlay.addWidget(topmost)
        self.overlay_check=QCheckBox('Рисовать путь поверх миникарты игры');self.overlay_check.setChecked(self.settings['minimap_overlay']['enabled']);self.overlay_check.toggled.connect(lambda v:self.overlay_setting('enabled',v));overlay.addWidget(self.overlay_check)
        of=QFormLayout()
        for key,label,lo,hi,dec in [('opacity','Непрозрачность пути',.2,1,2),('line_width','Толщина линии (пикс. экрана)',1,10,0)]:
            w=number(self.settings['minimap_overlay'][key],lo,hi,dec);w.valueChanged.connect(lambda v,k=key:self.overlay_setting(k,v));of.addRow(label,w)
        overlay.addLayout(of)
        self.overlay_status=self.text('Показывается во время навигации; следует за областью захвата, масштабом и поворотом. Окно пути не перехватывает мышь.',True);overlay.addWidget(self.overlay_status)
        self.build_game_map_settings(layout)
        self.navigation_fields={}
        for title,fields in [
            ('Обычная навигация',[('lead_m','Упреждение по расстоянию (м)',0,1500,0),('lead_s','Упреждение по скорости (с)',0,20,1)]),
            ('Стенограмма WRC',[('wrc_lead_m','Упреждение по расстоянию (м)',0,1500,0),('wrc_lead_s','Упреждение по скорости (с)',0,20,1),('wrc_chain_m','Интервал связки (м)',0,300,0)]),
            ('Общие подсказки',[('road_tolerance_m','Допуск от дороги (м)',5,200,0),('offroute_m','Порог перестроения при сходе (м)',20,500,0)])]:
            group=self.group(title,layout);form=QFormLayout()
            for key,label,lo,hi,decimals in fields:
                w=number(self.settings[key],lo,hi,decimals);w.valueChanged.connect(lambda value,k=key:self.set_setting(k,value));form.addRow(label,w);self.navigation_fields[key]=w
            group.addLayout(form)
            if title=='Обычная навигация':
                group.addWidget(self.text('Упреждение = расстояние + скорость × секунды. Подсказки на каждом реальном перекрёстке, между ними — расстояние по дороге.',True))
            elif title=='Общие подсказки':
                self.wrong_way_check=QCheckBox('Предупреждать о развороте при движении не туда');self.wrong_way_check.setChecked(self.settings['wrong_way_alert']);self.wrong_way_check.toggled.connect(lambda v:self.set_setting('wrong_way_alert',v));group.addWidget(self.wrong_way_check)
        speech=self.group('Озвучка',layout)
        self.voice_check=QCheckBox('Включить голосовые подсказки');self.voice_check.setChecked(self.settings['voice']);self.voice_check.toggled.connect(self.change_voice);speech.addWidget(self.voice_check)
        self.voice_backend=QComboBox()
        self.voice_backend.addItem('Авто · Yandex SpeechKit, если задан ключ, иначе голос Windows','auto')
        self.voice_backend.addItem('Только Yandex SpeechKit','speechkit')
        self.voice_backend.addItem('Только системный голос Windows','system')
        self.voice_backend.setCurrentIndex(max(0,self.voice_backend.findData(self.settings.get('voice_backend','auto'))))
        self.voice_backend.currentIndexChanged.connect(self.change_voice_backend);speech.addWidget(self.voice_backend)
        self.voice_combo=QComboBox();self.voice_combo.currentIndexChanged.connect(self.select_voice);speech.addWidget(self.voice_combo)
        self.voice_status=self.text('Загрузка системных голосов…',True);speech.addWidget(self.voice_status)
        sf=QFormLayout()
        for key,label,lo,hi,decimals in [('voice_rate','Темп речи (-1 медленно / 1 быстро)',-1,1,1)]:
            w=number(self.settings[key],lo,hi,decimals);w.valueChanged.connect(lambda value,k=key:self.set_setting(k,value));sf.addRow(label,w)
        speech.addLayout(sf);speech.addWidget(self.button('Прослушать режим',self.test_voice))
        hud=self.group('Значок навигации',layout);hf=QFormLayout();self.hud_fields={}
        for key,label,lo,hi,decimals in [('x','Экран X',-30000,30000,0),('y','Экран Y',-30000,30000,0),('width','Ширина',240,1200,0),('height','Высота',110,600,0),('opacity','Непрозрачность',.2,1,2)]:
            w=number(self.settings['hud'][key],lo,hi,decimals);w.valueChanged.connect(lambda value,k=key:self.hud_setting(k,value));self.hud_fields[key]=w;hf.addRow(label,w)
        hud.addLayout(hf)
        self.hud_lock=QCheckBox('Закрепить и пропускать клики мыши');self.hud_lock.setChecked(self.settings['hud']['locked']);self.hud_lock.toggled.connect(lambda v:self.hud_setting('locked',v));hud.addWidget(self.hud_lock)
        hud.addWidget(self.row(self.button('Показать / превью',self.show_hud),self.button('Вернуть на главный экран',self.reset_hud)))
        hud.addWidget(self.text('Игру лучше оставить в полноэкранном режиме, оверлей держится поверх. Отображение зависит от оптимизации полноэкранного режима Windows; эксклюзивный полный экран может его перекрыть. Позиция и размер — в логических пикселях Windows.',True))
        scale=self.group('Калибровка расстояния',layout);self.scale_label=self.text('');scale.addWidget(self.scale_label);self.update_scale_label()
        scale.addWidget(self.row(self.button('Калибровка по двум точкам',lambda:self.set_tool('calibrate')),self.button('Ввести м/пиксель',self.set_scale)))
        layout.addStretch()

    def notify(self,text):
        self.banner.setText(text)
        self.statusBar().showMessage(text,10000)

    def choose_map(self,index):
        map_id=self.map_combo.itemData(index)
        if map_id and map_id!=self.project['map']:
            if not self.switch_map(map_id):
                self.map_combo.blockSignals(True);self.map_combo.setCurrentIndex(self.map_combo.findData(self.project['map']));self.map_combo.blockSignals(False)

    def switch_map(self,map_id):
        if map_id==self.project['map']:return True
        try:
            target=load_map_project(map_id)
            if QPixmap(str(map_asset(map_id,'image'))).isNull() or not map_asset(map_id,'features').is_file():
                raise ValueError('Нет файлов этой карты — распакуйте портативный архив полностью')
        except (OSError,ValueError,TypeError,KeyError) as error:
            self.notify(f'Карта не переключена: {error}');return False
        self.autosave.stop()
        self.disconnect_recording();self.save_recording()
        if not self.save_project():return False
        self.stop_navigation(quiet=True);self.worker.capture=False;self.capture_button.setText('Включить позиционирование')
        self.reset_game_map()
        self.worker.request_map(map_id)
        self.settings['map_id']=map_id;self.project=target;self.history=[]
        self.load_recording()
        self.route=None;self.full_route=None;self.favorite_trip=None;self.frame=None
        self.frame_diagnostic=None;self.failure_diagnostic=None
        self.fix=None;self.fix_live=False;self.fix_time=0.;self.pending_start=False
        self.last_accepted=None;self.last_accepted_time=0.;self.jump_candidate=None;self.jump_count=0
        self.navigator=Navigator();self.calibration_points=[];self.draft_kinds=[]
        self.hud_problem=None;self.hud_navigation=None;self.refresh_hud()
        self.map.project=target;self.map.load_map(map_id);self.drawing_bar.hide();self.favorite_options.hide();self.favorite_preview_label.hide();self.draft_preview=None
        self.map_combo.blockSignals(True);self.map_combo.setCurrentIndex(self.map_combo.findData(map_id));self.map_combo.blockSignals(False)
        self.sync_policy();self.refresh_lists();self.refresh_map();self.update_scale_label()
        self.preview.clear();self.preview.setText('После включения позиционирования здесь появится превью миникарты')
        self.set_localization('Позиция не определена');self.fix_detail.setText('Карта переключена, готовим признаки для позиционирования.')
        self.route_label.setText('Маршрут ещё не построен');self.coordinate_label.setText('Игровые координаты —')
        self.coordinate_input.clear();self.coordinate_feedback.setText('Карта переключена, введите игровые координаты для этой карты.')
        name=map_info(map_id)['name'];self.setWindowTitle('Навигатор WARDOGS '+__version__+' · '+name)
        self.save_settings();self.notify(f'Переключено на {name}; дороги, маршруты и стенограмма загружены. Включите позиционирование и начните навигацию заново.')
        return True

    def worker_ready(self,map_id,generation):
        if (map_id,generation)!=self.worker.context or map_id!=self.project['map']:return
        self.fix_detail.setText('Миникарта: север всегда сверху; проверьте превью захвата в настройках.')
        self.statusBar().showMessage(map_info(map_id)['name']+' — визуальное позиционирование готово · включите позиционирование')

    def reload_roads(self):
        if QMessageBox.question(self,'Загрузка дорог','Заменить текущие дороги встроенной сетью? Цели, опасные зоны и стенограмма сохранятся. Текущая конфигурация будет сохранена в резервную копию; отменить можно кнопкой «Отменить».')!=QMessageBox.Yes:return
        atomic_json(user_dir()/f'project-before-road-reload-{time.time_ns()}.json',self.project)
        self.snapshot();latest=read_project(map_asset(self.project['map'],'project'))
        self.project['roads']=latest['roads'];self.project['road_data_revision']=latest['road_data_revision'];self.changed();self.notify('Встроенные дороги загружены; прежняя конфигурация в резервной копии')

    def snapshot(self):
        self.history.append(deepcopy(self.project));self.history=self.history[-30:]

    def changed(self,replan=True):
        self.recorder.set_roads(self.project['roads'])
        resume=self.navigator.active and self.fix_live and self.fix and self.fix.valid
        leg,lap=self.navigator.leg,self.navigator.lap
        anchors=None
        if resume:
            waypoints=list(self.project['waypoints'])
            if lap%2:waypoints.reverse()
            remaining=[]
            for p in waypoints:
                q=nearest_on_route(p,self.navigator.route.points)
                if q is None or q[0]>25 or q[1]>self.navigator.progress+3:remaining.append(p)
            goal=self.project['start'] if lap%2 else self.project['destination']
            if goal:anchors=[[self.fix.x,self.fix.y],*remaining,goal]
        self.stop_navigation(quiet=True)
        self.pending_start=False
        self.route=None;self.full_route=None;self.map.route=None
        self.refresh_lists();self.refresh_map();self.autosave.start()
        if replan and self.project['destination'] and (self.project['start'] or (self.fix and self.fix.valid) or self.active_favorite()):
            success=self.plan_route(quiet=True,anchors=anchors)
            if resume and success:
                self.start_trip_navigation(lap,leg)
                self.update_minimap_overlay()
                self.speak('Маршрут обновлён')

    def save_project(self):
        try:atomic_json(project_path(self.project['map']),validate_project(self.project));return True
        except (OSError,ValueError,TypeError) as error:self.notify(f'Не удалось сохранить: {error}');return False

    def save_settings(self):
        try:atomic_json(user_dir()/'settings.json',self.settings)
        except (OSError,ValueError) as error:self.notify(f'Не удалось сохранить настройки: {error}')

    def refresh_lists(self):
        self.road_list.blockSignals(True);selected=self.map.selected_road;self.road_list.clear()
        for r in self.project['roads']:self.road_list.addItem(f"{r['name']}  /  {KINDS[r['kind']]}"+('  [мост]' if r.get('bridge') else ''))
        self.road_list.setCurrentRow(next((i for i,r in enumerate(self.project['roads']) if r['id']==selected),-1));self.road_list.blockSignals(False)
        self.note_list.clear()
        for n in self.project['notes']:self.note_list.addItem(f"{NOTE_TYPES[n['type']]} {n['grade'] if n['type'] in ('left','right') else ''} · {n.get('text') or 'стандартная подсказка'}")
        self.avoid_list.clear()
        for i,z in enumerate(self.project['avoid']):
            scale=self.project['meters_per_pixel'] or 1
            size=f"{z['width']*scale:.0f} × {z['height']*scale:.0f}" if z.get('shape')=='rect' else f"радиус {z['radius']*scale:.0f}"
            self.avoid_list.addItem(f"Опасная зона {i+1} · {size} {'м' if self.project['meters_per_pixel'] else 'ед.'}")
        self.waypoint_list.clear()
        for i,p in enumerate(self.project['waypoints']):self.waypoint_list.addItem(f'{i+1:02}  точка '+format_coordinates(map_to_game(self.project['map'],p)))
        self.waypoint_list.setVisible(bool(self.project['waypoints']))
        for button in self.clear_waypoint_buttons:button.setEnabled(bool(self.project['waypoints']))
        for button in self.clear_avoid_buttons:button.setEnabled(bool(self.project['avoid']))
        self.saved_dest.blockSignals(True);self.saved_dest.clear();self.saved_dest.addItem('Выберите сохранённую цель')
        for d in self.project['destinations']:self.saved_dest.addItem(d['name'])
        self.saved_dest.blockSignals(False)
        start=self.project['start']
        self.start_label.setText('Старт / конец обратного пути  '+format_coordinates(map_to_game(self.project['map'],start)) if start else 'Старт не задан · при запуске навигации берётся текущая позиция')
        d=self.project['destination'];label='Цель (туда)' if self.project['roundtrip'] else 'Цель'
        self.destination_label.setText(label+'  '+format_coordinates(map_to_game(self.project['map'],d)) if d else 'Укажите цель на карте')
        selected=self.favorite_list.currentRow();self.favorite_list.clear()
        for saved in self.project['route_library']:
            active='▶ ' if saved['id']==self.project.get('active_route_id') else ''
            self.favorite_list.addItem(active+saved['name'])
        if selected>=0:self.favorite_list.setCurrentRow(min(selected,self.favorite_list.count()-1))
        active=self.active_favorite()
        self.favorite_status.setText(('Текущий маршрут: '+active['name']+(' · обратно' if self.project.get('active_route_reverse') else '')) if active else 'Сейчас — автопостроение')

    def refresh_map(self):
        self.update_favorite_preview()
        self.map.project=self.project;self.map.route=self.route;self.map.full_route=self.full_route;self.map.fix=self.fix;self.map.redraw()
        self.update_big_overlay()

    def favorite_drawing_option(self,*_):
        self.update_drawing_controls()
        self.refresh_map()

    def update_drawing_controls(self):
        follow=self.map.tool=='favorite' and self.favorite_snap.isChecked()
        self.favorite_build.setVisible(not self.favorite_snap.isChecked())
        for widget in (self.draw_kind_label,self.draw_kind,self.draw_snap):
            widget.setVisible(not follow);widget.setEnabled(not follow)

    def update_favorite_preview(self):
        self.draft_preview=None;self.map.draft_path=[]
        if self.map.tool!='favorite':return
        if not self.favorite_snap.isChecked():
            self.favorite_preview_label.setText('Путь сохраняется как нарисован; '+('недостающие участки будут добавлены в общую сеть.' if self.favorite_build.isChecked() else 'используется только в этом маршруте, общие дороги не создаются.'))
            return
        if len(self.map.draft)<2:
            self.favorite_preview_label.setText('Грубо отметьте старт, промежуточные точки и финиш; зелёная линия пройдёт по разрешённым дорогам.')
            return
        try:
            raw=dict(id='preview',name='Превью рисования',points=self.map.draft,kinds=self.draft_kinds,control_points=self.map.draft)
            self.draft_preview=snap_saved(self.project['roads'],raw,self.project['policy'],self.project['avoid'])
            points=self.draft_preview['points'];self.map.draft_path=points
            length=sum(distance(a,b) for a,b in zip(points,points[1:]))*(self.project['meters_per_pixel'] or 1)
            self.favorite_preview_label.setText(f'Превью по дорогам · {length:.0f} {plural(round(length),"метр","метра","метров") if self.project["meters_per_pixel"] else "ед. карты"} · опорных точек: {len(self.map.draft)}; Enter сохраняет путь без новых общих дорог.')
        except RouteError as error:self.favorite_preview_label.setText(str(error)+'; сделайте шаг назад или измените правила дорог.')

    def set_tool(self,tool):
        if self.map.tool!=tool:
            self.map.draft=[];self.draft_kinds=[]
        self.map.set_tool(tool)
        self.drawing_bar.setVisible(tool in ('road','favorite'))
        self.favorite_options.setVisible(tool=='favorite');self.favorite_preview_label.setVisible(tool=='favorite')
        self.update_drawing_controls()
        tips={'pan':'Перетаскивание — сдвиг, колесо — масштаб; щелчок выбирает дорогу и её точки, двойной щелчок — свойства, ПКМ — удаление','destination':'Щёлкните на карте, чтобы задать цель (сохранённый маршрут отключится)','waypoint':'Щелчок добавляет путевую точку; порядок точек — порядок проезда (сохранённый маршрут отключится)','avoid':'Зажмите кнопку мыши и выделите опасную зону — после отпускания маршрут её обойдёт','road':'Выберите тип и ставьте точки по дороге или бездорожью; Enter / двойной щелчок — готово, Backspace — шаг назад','favorite':'Ставьте точки полного пути; можно привязать к дорогам или выбрать, достраивать ли недостающие; Enter — сохранить','note':'Щёлкните рядом с маршрутом, чтобы добавить метку стенограммы WRC','calibrate':'По очереди отметьте два ориентира с известным расстоянием'}
        tips['start']='Щёлкните на карте, чтобы задать старт; в челноке это конец обратного пути, точку можно перетащить в любой момент'
        self.notify(tips[tool])
        if tool=='calibrate':self.calibration_points=[]
        self.refresh_map()

    def cancel_tool(self):
        self.map.draft=[];self.draft_kinds=[];self.calibration_points=[];self.map.selected_road=None;self.set_tool('pan');self.refresh_map()

    def undo_draw_point(self):
        if self.map.tool in ('road','favorite') and self.map.draft:
            self.map.draft.pop()
            if self.draft_kinds:self.draft_kinds.pop()
            self.refresh_map()

    def active_favorite(self):
        return next((r for r in self.project['route_library'] if r['id']==self.project.get('active_route_id')),None)

    def detach_favorite(self):
        self.project.pop('active_route_id',None);self.project.pop('active_route_reverse',None);self.favorite_trip=None

    def clear_favorite(self):
        self.snapshot();self.detach_favorite();self.changed();self.notify('Автопостроение по цели и путевым точкам восстановлено')

    def map_click(self,x,y):
        p=[x,y];tool=self.map.tool
        if tool=='start':self.set_start(p);self.set_tool('pan')
        elif tool=='destination':self.snapshot();self.detach_favorite();self.project['destination']=p;self.changed();self.set_tool('pan')
        elif tool=='waypoint':self.snapshot();self.detach_favorite();self.project['waypoints'].append(p);self.changed()
        elif tool=='avoid':
            radius,ok=QInputDialog.getInt(self,'Зона объезда','Радиус (м)',150,10,3000,10)
            if ok:self.snapshot();self.project['avoid'].append({'point':p,'radius':radius/(self.project['meters_per_pixel'] or 1)});self.changed();self.set_tool('pan')
        elif tool in ('road','favorite'):
            # Snap to existing segment: a vertex explicitly added here joins it.
            from .routing import project
            options=[project(p,a,b) for r in self.project['roads'] for a,b in zip(r['points'],r['points'][1:])]
            if options and self.draw_snap.isChecked() and not(tool=='favorite' and self.favorite_snap.isChecked()):
                best=min(options,key=lambda q:q[0])
                if best[0]<8:p=list(best[1])
            if not self.map.draft or distance(p,self.map.draft[-1])>1:
                if self.map.draft:self.draft_kinds.append(self.draw_kind.currentData())
                self.map.draft.append(p)
            self.refresh_map()
        elif tool=='note':
            dialog=NoteDialog(parent=self)
            if self.route:
                projection=nearest_on_route(p,self.route.points)
                if projection:
                    _,_,i,_=projection;dialog.bearing.setValue(round(bearing(self.route.points[i],self.route.points[i+1])))
            if dialog.exec()==QDialog.Accepted:
                self.snapshot();self.project['notes'].append(dict(id=uid(),point=p,**dialog.values()));self.changed();self.set_tool('pan')
        elif tool=='calibrate':
            self.calibration_points.append(p);self.map.draft=list(self.calibration_points);self.refresh_map()
            if len(self.calibration_points)==2:
                length=distance(*self.calibration_points)
                if length<5:self.notify('Точки слишком близко, откалибруйте заново');self.cancel_tool();return
                meters,ok=QInputDialog.getDouble(self,'Калибровка расстояния','Расстояние между точками в игре (м)',1000,1,30000,1)
                if ok:
                    self.snapshot();self.project['meters_per_pixel']=meters/length;self.project['calibration']={'source':'Калибровка по двум точкам','points':self.calibration_points,'distance_m':meters,'status':'Откалибровано пользователем'};self.changed();self.update_scale_label()
                self.cancel_tool()

    def finish_road(self):
        if self.map.tool=='favorite':self.finish_favorite();return
        if self.map.tool!='road' or len(self.map.draft)<2:return
        dialog=RoadDialog({'kind':self.draw_kind.currentData(),'points':self.map.draft},self)
        if dialog.exec()==QDialog.Accepted:
            self.snapshot();self.project['roads'].append(dict(id=uid(),points=deepcopy(self.map.draft),**dialog.values()));self.map.draft=[];self.changed();self.set_tool('pan')

    def finish_favorite(self):
        if len(self.map.draft)<2:return
        self.update_favorite_preview()
        if self.favorite_snap.isChecked() and self.draft_preview is None:
            self.notify(self.favorite_preview_label.text());return
        name,ok=QInputDialog.getText(self,'Сохранить полный путь','Название маршрута')
        if not ok or not name.strip():return
        saved=deepcopy(self.draft_preview) if self.favorite_snap.isChecked() else dict(points=deepcopy(self.map.draft),kinds=list(self.draft_kinds),snap_to_roads=False,build_roads=self.favorite_build.isChecked())
        saved.update(id=uid(),name=name.strip()[:200])
        if self.store_favorite(saved):self.cancel_tool()

    def store_favorite(self,saved):
        saved=deepcopy(saved);saved.setdefault('snap_to_roads',False);saved.setdefault('build_roads',True)
        candidate=deepcopy(self.project);candidate['route_library'].append(saved)
        count=supplement_roads(candidate['roads'],saved) if saved['build_roads'] else 0
        try:candidate=validate_project(candidate)
        except ValueError as error:self.notify('Маршрут не сохранён: '+str(error));return False
        self.snapshot();self.project=candidate;self.refresh_lists();self.refresh_map();self.autosave.start()
        self.favorite_list.setCurrentRow(len(self.project['route_library'])-1)
        detail=f'Добавлено {count} {plural(count,"дорога","дороги","дорог")}. ' if saved['build_roads'] else 'Общие дороги не добавлены. '
        self.notify('Полный путь сохранён; '+detail+'Загрузить его можно во вкладке «Сохранённые маршруты».')
        return True

    def save_current_route(self):
        if not self.route:self.notify('Сначала постройте или покажите маршрут');return
        name,ok=QInputDialog.getText(self,'Сохранить текущий маршрут','Название маршрута')
        if ok and name.strip():
            route=self.route
            active=self.active_favorite() or {}
            self.store_favorite(saved_route(name.strip()[:200],route,snap_to_roads=active.get('snap_to_roads',False),build_roads=active.get('build_roads',True)))
            self.route=route;self.refresh_map()

    def load_favorite(self,reverse=False):
        i=self.favorite_list.currentRow()
        if i<0:self.notify('Сначала выберите сохранённый маршрут');return
        saved=self.project['route_library'][i]
        self.snapshot();count=supplement_roads(self.project['roads'],saved) if saved.get('build_roads',True) else 0
        self.favorite_trip=None
        self.project['active_route_id']=saved['id'];self.project['active_route_reverse']=bool(reverse)
        self.project['start']=list(saved['points'][-1 if reverse else 0])
        self.project['destination']=list(saved['points'][0 if reverse else -1]);self.project['waypoints']=[]
        self.changed(replan=False)
        if self.plan_route(preview_full=True,quiet=True):
            detail=f'Добавлено {count} {plural(count,"дорога","дороги","дорог")}. ' if saved.get('build_roads',True) else 'Только для этого маршрута, общие дороги не созданы. '
            self.notify(f"Загружен маршрут «{saved['name']}» со стартом и финишем; {detail}При запуске навигации он подключится к текущей позиции, старт можно изменить.")
        else:self.notify(self.route_label.text())

    def rename_favorite(self):
        i=self.favorite_list.currentRow()
        if i<0:return
        saved=self.project['route_library'][i]
        name,ok=QInputDialog.getText(self,'Переименовать маршрут','Название',text=saved['name'])
        if ok and name.strip():self.snapshot();saved['name']=name.strip()[:200];self.refresh_lists();self.autosave.start()

    def delete_favorite(self):
        i=self.favorite_list.currentRow()
        if i<0:return
        self.snapshot();saved=self.project['route_library'].pop(i)
        if saved['id']==self.project.get('active_route_id'):self.detach_favorite()
        self.changed()

    def import_library(self,kind):
        label='дорог' if kind=='roads' else 'маршрутов'
        path,_=QFileDialog.getOpenFileName(self,'Импорт '+label,'','JSON (*.json)')
        if not path:return
        try:
            incoming=read_project(path);check_library_target(self.project,incoming,kind)
            options={}
            if kind=='routes':
                dialog=FavoriteImportDialog(incoming['route_library'],self)
                if dialog.exec()!=QDialog.Accepted:return
                options=dialog.options()
            data,count,supplemented=merge_library(self.project,incoming,kind,**options)
        except (OSError,ValueError,TypeError,KeyError) as error:self.notify(f'Не удалось импортировать: {error}');return
        self.snapshot();self.project=data;self.changed()
        self.notify(f'Объединено элементов ({label}): {count}, добавлено {supplemented} {plural(supplemented,"дорога","дороги","дорог")}; прежние метки сохранены, можно отменить')

    def export_library(self,kind):
        label='дорог' if kind=='roads' else 'маршрутов'
        path,_=QFileDialog.getSaveFileName(self,'Экспорт '+label,map_info(self.project['map'])['name']+'-'+('дороги' if kind=='roads' else 'маршруты')+'.json','JSON (*.json)')
        if not path:return
        try:atomic_json(path,library_payload(self.project,kind));self.notify('Экспорт '+label+' выполнен: '+path)
        except (OSError,ValueError,TypeError) as error:self.notify(f'Не удалось экспортировать: {error}')

    def select_road(self,index):
        self.map.selected_road=self.project['roads'][index]['id'] if 0<=index<len(self.project['roads']) else None;self.refresh_map()

    def select_road_on_map(self,road_id):
        index=next((i for i,r in enumerate(self.project['roads']) if r['id']==road_id),-1)
        if self.map.tool!='pan':self.set_tool('pan')
        self.road_list.blockSignals(True);self.road_list.setCurrentRow(index);self.road_list.blockSignals(False)
        self.select_road(index)
        if index>=0:
            self.road_list.scrollToItem(self.road_list.item(index))
            self.notify('Выбрана дорога «'+self.project['roads'][index]['name']+'»; тяните узлы для правки, двойной щелчок — свойства, ПКМ — удаление.')

    def edit_road_on_map(self,road_id):
        self.select_road_on_map(road_id);self.edit_road()

    def delete_road_on_map(self,road_id):
        self.select_road_on_map(road_id);self.delete_road()

    def edit_road(self):
        i=self.road_list.currentRow()
        if i<0:return
        dialog=RoadDialog(self.project['roads'][i],self)
        if dialog.exec()==QDialog.Accepted:
            self.snapshot();self.project['roads'][i].update(dialog.values());self.project['roads'][i].pop('recording',None);self.changed()

    def delete_road(self):
        i=self.road_list.currentRow()
        if i>=0:self.snapshot();self.project['roads'].pop(i);self.map.selected_road=None;self.changed()

    def edit_note(self):
        i=self.note_list.currentRow()
        if i<0:return
        dialog=NoteDialog(self.project['notes'][i],self)
        if dialog.exec()==QDialog.Accepted:self.snapshot();self.project['notes'][i].update(dialog.values());self.changed()

    def delete_note(self):
        i=self.note_list.currentRow()
        if i>=0:self.snapshot();self.project['notes'].pop(i);self.changed()

    def edit_avoid(self):
        i=self.avoid_list.currentRow()
        if i<0:return
        zone=self.project['avoid'][i];scale=self.project['meters_per_pixel'] or 1
        if zone.get('shape')=='rect':
            width,ok=QInputDialog.getDouble(self,'Опасная зона','Ширина (м)',zone['width']*scale,10,10000,0)
            if not ok:return
            height,ok=QInputDialog.getDouble(self,'Опасная зона','Высота (м)',zone['height']*scale,10,10000,0)
            if ok:self.snapshot();zone.update(width=width/scale,height=height/scale);self.changed()
            return
        value,ok=QInputDialog.getInt(self,'Зона объезда','Радиус (м)',round(zone['radius']*scale),10,3000)
        if ok:self.snapshot();zone['radius']=value/scale;self.changed()

    def delete_avoid(self):
        i=self.avoid_list.currentRow()
        if i>=0:self.snapshot();self.project['avoid'].pop(i);self.changed()

    def add_danger_region(self,region):
        def apply():
            self.snapshot();self.project['avoid'].append(region);self.changed();self.notify('Опасная зона сохранена. '+('Объезд найден.' if self.route else self.route_label.text())+' Тяните центр или правый нижний угол, чтобы изменить.')
        QTimer.singleShot(0,apply)

    def delete_waypoint(self):
        i=self.waypoint_list.currentRow()
        if i>=0:self.snapshot();self.project['waypoints'].pop(i);self.changed()

    def clear_button(self,key,location):
        waypoint=key=='waypoints'
        label='Очистить путевые точки' if waypoint else 'Очистить опасные зоны'
        button=self.button(label,lambda:self.clear_annotations(key))
        button.setObjectName('clear_'+key+'_'+location)
        button.setToolTip('Очищает только '+('путевые точки' if waypoint else 'опасные зоны')+' текущей карты; цель, дороги и маршруты остаются, можно отменить')
        (self.clear_waypoint_buttons if waypoint else self.clear_avoid_buttons).append(button)
        return button

    def clear_annotations(self,key):
        if key not in ('waypoints','avoid') or not self.project[key]:return
        count=len(self.project[key]);self.snapshot();self.project[key]=[];self.changed()
        self.notify(f"Удалено: {count} {plural(count,'путевая точка','путевые точки','путевых точек') if key=='waypoints' else plural(count,'опасная зона','опасные зоны','опасных зон')}; можно вернуть кнопкой «Отменить»")

    def clear_route_button(self,location):
        button=self.button('Сбросить маршрут',self.clear_current_route)
        button.setObjectName('clear_current_route_'+location)
        button.setToolTip('Останавливает навигацию и сбрасывает старт, цель, путевые точки и маршрут; видимость значка, дороги, сохранённые маршруты, опасные зоны и правила остаются')
        return button

    def clear_current_route(self):
        if self.project['start'] is not None or self.project['destination'] is not None or self.project['waypoints'] or self.active_favorite():self.snapshot()
        self.project['start']=None;self.project['destination']=None;self.project['waypoints']=[];self.detach_favorite()
        self.changed(replan=False)
        self.navigator=Navigator();self.minimap_overlay.points=[];self.minimap_overlay.full_points=[];self.worker.path_mask=None
        self.route_label.setText('Маршрут сброшен; выберите новую цель')
        self.set_hud({'state':'idle','text':'Маршрут сброшен'})
        self.notify('Маршрут, старт, цель и путевые точки сброшены, навигация остановлена; поездку можно вернуть кнопкой «Отменить»')

    def set_start(self,point):
        self.snapshot();self.project['start']=list(point);self.favorite_trip=None;self.changed()
        self.notify('Старт обновлён; в челноке возврат будет сюда, смена цели старт не меняет')

    def use_current_start(self):
        if not (self.fix_live and self.fix and self.fix.valid and time.monotonic()-self.fix_time<2.5):
            self.notify('Сначала включите позиционирование и дождитесь точной позиции, затем задайте старт');return
        self.set_start([self.fix.x,self.fix.y])

    def move_item(self,key,p):
        # The scene item emits on mouse release; defer rebuilding its scene until
        # the event handler has returned to Qt.
        def apply():
            self.snapshot();kind=key[0]
            if kind=='road':
                road=next(r for r in self.project['roads'] if r['id']==key[1]);old=road['points'][key[2]]
                ports=[q for r in self.project['roads'] for q in bridge_ports(r)]
                connected=[r for r in self.project['roads'] if roads_connect(road,r,old,ports)]
                for r in connected:
                    if any(distance(old,q)<1.5 for q in r['points']):r.pop('recording',None)
                    r['points']=[list(p) if distance(old,q)<1.5 else q for q in r['points']]
            elif kind=='start':self.project['start']=p;self.favorite_trip=None
            elif kind=='destination':self.detach_favorite();self.project['destination']=p
            elif kind=='waypoint':self.detach_favorite();self.project['waypoints'][key[1]]=p
            elif kind=='note':self.project['notes'][key[1]]['point']=p
            elif kind=='avoid':self.project['avoid'][key[1]]['point']=p
            elif kind=='avoid_resize':
                z=self.project['avoid'][key[1]];z['width']=max(4,abs(p[0]-z['point'][0])*2);z['height']=max(4,abs(p[1]-z['point'][1])*2)
            self.changed()
        QTimer.singleShot(0,apply)

    def remove_item(self,key):
        def apply():
            self.snapshot();kind=key[0]
            if kind=='road':
                road=next(r for r in self.project['roads'] if r['id']==key[1])
                if len(road['points'])<=2:self.notify('В дороге должно быть не меньше двух точек; удалить дорогу целиком можно в списке');return
                road['points'].pop(key[2]);road.pop('recording',None)
            elif kind=='start':
                self.project['start']=None;self.favorite_trip=None;self.stop_navigation(quiet=True)
            elif kind=='destination':self.detach_favorite();self.project['destination']=None
            elif kind=='waypoint':self.detach_favorite();self.project['waypoints'].pop(key[1])
            elif kind=='note':self.project['notes'].pop(key[1])
            elif kind in ('avoid','avoid_resize'):self.project['avoid'].pop(key[1])
            self.changed()
        QTimer.singleShot(0,apply)

    def toggle_roads(self,value):self.map.show_roads=value;self.refresh_map()

    def undo(self):
        if not self.history:self.notify('Нечего отменять');return
        self.project=self.history.pop();self.favorite_trip=None;self.sync_policy();self.changed();self.update_scale_label();self.notify('Последнее действие отменено')

    def sync_policy(self):
        for k,w in self.kind_preferences.items():w.blockSignals(True);w.setCurrentIndex(w.findData(self.project['policy']['preferences'][k]));w.blockSignals(False)
        self.roundtrip.blockSignals(True);self.roundtrip.setChecked(self.project['roundtrip']);self.roundtrip.blockSignals(False)

    def change_policy(self):
        if not hasattr(self,'kind_preferences'):return
        self.snapshot();self.project['policy']={'allowed':list(KINDS),'preferences':{k:w.currentData() for k,w in self.kind_preferences.items()},'confirmed_only':False};self.project['roundtrip']=self.roundtrip.isChecked();self.changed()

    def choose_destination(self,index):
        if index>0:self.snapshot();self.detach_favorite();self.project['destination']=list(self.project['destinations'][index-1]['point']);self.changed()

    def save_destination(self):
        if not self.project['destination']:self.notify('Сначала укажите цель на карте');return
        name,ok=QInputDialog.getText(self,'Сохранить цель','Название')
        if ok and name.strip():self.snapshot();self.project['destinations'].append({'name':name.strip(),'point':list(self.project['destination'])});self.changed()

    def import_project(self):
        path,_=QFileDialog.getOpenFileName(self,'Импорт настроек навигации','','Настройки JSON (*.json)')
        if not path:return
        try:data=read_project(path)
        except (OSError,ValueError,TypeError,KeyError) as error:self.notify(f'Настройки не импортированы: {error}');return
        if data['map']!=self.project['map'] and not self.switch_map(data['map']):return
        self.snapshot();self.project=data;self.favorite_trip=None;self.sync_policy();self.changed();self.update_scale_label();self.notify('Настройки импортированы, можно отменить')

    def export_project(self):
        path,_=QFileDialog.getSaveFileName(self,'Экспорт настроек навигации',map_info(self.project['map'])['name']+'-настройки.json','Настройки JSON (*.json)')
        if not path:return
        try:atomic_json(path,validate_project(self.project));self.notify('Настройки экспортированы: '+path)
        except (OSError,ValueError,TypeError) as error:self.notify(f'Не удалось экспортировать: {error}')

    def preview_route(self):
        if self.navigator.active:
            if self.fix_live and self.fix and self.fix.valid:self.replan_navigation()
            else:self.notify('Маршрут пересчитается, когда появится точная текущая позиция')
        else:self.plan_route(preview_full=True)

    def plan_route(self,*_,quiet=False,anchors=None,preview_full=False):
        saved=self.active_favorite()
        returning=bool(anchors is not None and self.navigator.lap%2)
        current=anchors[0] if anchors else None
        if anchors is None and not saved:
            origin=self.project['start'] or ([self.fix.x,self.fix.y] if self.fix and self.fix.valid else None)
            if origin is None:
                if not quiet:self.notify('Сначала задайте старт или включите позиционирование')
                return False
            if not self.project['destination']:
                if not quiet:self.notify('Сначала укажите цель на карте')
                return False
            anchors=[origin,*self.project['waypoints'],self.project['destination']]
        complete=True
        try:
            p=self.project['policy']
            if saved:
                journey=self.favorite_trip if self.favorite_trip and not preview_full else saved
                if journey is self.favorite_trip:
                    self.route=follow_saved(self.project['roads'],journey,p,self.project['avoid'],current,returning)
                    self.full_route=self.route
                    if current is not None:
                        try:self.full_route=follow_saved(self.project['roads'],journey,p,self.project['avoid'],reverse=returning)
                        except RouteError:complete=False
                else:
                    full=follow_saved(self.project['roads'],journey,p,self.project['avoid'],self.project['start'],self.project.get('active_route_reverse',False))
                    trip=saved_route('Текущий полный челнок',full,snap_to_roads=saved.get('snap_to_roads',False),build_roads=saved.get('build_roads',True))
                    self.full_route=full.reversed() if returning else full
                    self.route=follow_saved(self.project['roads'],trip,p,self.project['avoid'],current,returning) if current is not None else self.full_route
                    if self.project['start'] is not None and not preview_full:self.favorite_trip=trip
            else:
                self.route=plan(self.project['roads'],anchors,p['allowed'],p['confirmed_only'],self.project['avoid'],preferences=p.get('preferences'))
                self.full_route=self.route
                if self.project['start'] is not None:
                    full_anchors=[self.project['start'],*self.project['waypoints'],self.project['destination']]
                    if returning:full_anchors.reverse()
                    if full_anchors!=anchors:
                        try:self.full_route=plan(self.project['roads'],full_anchors,p['allowed'],p['confirmed_only'],self.project['avoid'],preferences=p.get('preferences'))
                        except RouteError:complete=False
        except RouteError as error:
            self.route=None;self.full_route=None;self.route_label.setText(str(error));self.refresh_map()
            if not quiet:self.notify(str(error))
            return False
        scale=self.project['meters_per_pixel'];length=self.route.length*(scale or 1)
        total=f'{length/1000:.2f} km' if scale else f'{length:.0f} ед. карты'
        self.route_label.setText(f"{total} · путевых точек: {len(self.project['waypoints'])}\nСмещение привязки концов {max(self.route.snap_distances)*(scale or 1):.0f} {'м' if scale else 'ед.'}")
        if not complete:self.route_label.setText(self.route_label.text()+'\nПолный маршрут пока недостижим, показан доступный участок; старт не изменён')
        self.refresh_map()
        if not quiet:self.notify('Построено по сохранённому маршруту, опасные зоны объезжаются локально' if saved else 'Маршрут построен; зелёные точки можно перетаскивать')
        return True

    def preview_cues(self):
        if not self.route:self.notify('Сначала покажите маршрут');return
        dialog=QDialog(self);dialog.setWindowTitle('Подсказки · текущий маршрут');dialog.resize(700,550)
        layout=QVBoxLayout(dialog)
        layout.addWidget(self.text('Один маршрут в двух режимах; расстояние считается от старта вдоль маршрута. Расчётную сложность поворотов WRC можно переопределить в разметке карты.',True))
        pages=QTabWidget();layout.addWidget(pages)
        scale=self.project['meters_per_pixel'] or 1
        lists={}
        for mode,label in [('normal','Обычный · перекрёстки и расстояния'),('wrc','WRC · непрерывная стенограмма')]:
            listing=QListWidget();cues=cues_for(self.route,self.project['notes'],mode,scale)
            if mode=='normal' and cues:listing.addItem('Старт: двигайтесь по текущей дороге'+distance_text(cues[0].at*scale,self.project['meters_per_pixel'] is not None))
            for cue in cues:
                listing.addItem(f'{cue.at*scale:.0f} '+('м' if self.project['meters_per_pixel'] else 'ед.')+'  ·  '+cue_text(cue,mode,spoken=True)+('  [расчёт]' if cue.inferred else ''))
            pages.addTab(listing,label);lists[mode]=listing
        pages.setCurrentIndex(1 if self.settings['mode']=='wrc' else 0)
        layout.addWidget(self.button('Прослушать выбранное',lambda:self.speak(pages.currentWidget().currentItem().text().replace('[расчёт]','')) if pages.currentWidget().currentItem() else None))
        layout.addWidget(self.button('Закрыть',dialog.accept));dialog.exec()

    def toggle_capture(self):
        self.reset_game_map();self.worker.reset_view()
        if self.worker.capture or (self.hud_notice and self.hud_notice.get('state')=='capture_off'):self.clear_hud_notice()
        self.worker.capture=not self.worker.capture
        if self.worker.capture:
            self.failure_diagnostic=None
            self.set_localization('Определяем позицию')
            if not self.navigator.active:self.set_hud({'state':'locating','text':'Ожидание позиции'})
            self.worker.wake.set();self.capture_button.setText('Выключить позиционирование');self.notify('Считываем выбранную область; миникарта игры должна быть видна')
        else:
            self.stop_navigation(quiet=True);self.invalidate_fix('Позиционирование выключено')
            self.set_localization('Позиционирование выключено');self.set_hud({'state':'capture_off','text':'Позиционирование выключено'})

    def start_navigation(self):
        if not self.project['destination']:self.notify('Сначала выберите цель');return
        if not self.worker.isRunning():self.notify('Визуальное позиционирование не готово — дождитесь запуска или перезапустите');return
        self.pending_start=True
        if not self.worker.capture:self.toggle_capture()
        if self.fix_live and self.fix and self.fix.valid and time.monotonic()-self.fix_time<2.5:self.begin_navigation()
        else:self.notify('Получаем текущую позицию; навигация начнётся автоматически')

    def begin_navigation(self):
        self.pending_start=False
        if not (self.fix and self.fix.valid):return
        self.favorite_trip=None;self.navigator.lap=0
        if self.project['start'] is None:
            self.snapshot();self.project['start']=[self.fix.x,self.fix.y];self.autosave.start();self.refresh_lists();self.refresh_map()
        anchors=[[self.fix.x,self.fix.y],*self.project['waypoints'],self.project['destination']]
        if not self.plan_route(quiet=True,anchors=anchors):
            self.set_hud({'state':'offroute','text':'Нет проходимого маршрута'});self.notify(self.route_label.text());return
        self.start_trip_navigation()
        self.update_minimap_overlay()
        self.hud.show();self.notify('Навигация запущена · при потере позиции подсказки ставятся на паузу')

    def start_trip_navigation(self,lap=0,leg='путь туда',preserve_arrival=False):
        lock=self.navigator.arrival_lock if preserve_arrival else None
        goal=self.project['start'] if lap%2 else self.project['destination']
        return_goal=self.project['destination'] if lap%2 else self.project['start']
        self.navigator.start(self.route,self.project['notes'],self.settings,self.project['meters_per_pixel'],self.project['roundtrip'],goal,return_goal)
        self.navigator.lap=lap;self.navigator.leg=leg;self.navigator.arrival_lock=lock
        self.hud_navigation=None;self.refresh_hud()

    def road_gap(self,point):
        extra=(self.full_route or self.route) if self.active_favorite() else None
        return distance_to_roads(point,self.project['roads'],self.project['policy']['allowed'],self.project['avoid'],extra)*(self.project['meters_per_pixel'] or 1)

    def stop_navigation(self,*_,quiet=False):
        self.big_resume=None
        self.minimap_overlay.hide()
        self.navigator.stop();self.pending_start=False;self.tts.stop()
        self.hud_navigation=None;self.clear_hud_notice();self.refresh_hud()
        if not quiet:self.flash_hud('Навигация остановлена');self.notify('Навигация остановлена')

    def worker_error(self,text,map_id=None,generation=0):
        if map_id is not None and ((map_id,generation)!=self.worker.context or map_id!=self.project['map']):return
        self.hide_game_map()
        self.fix=Fix(reason=text,map_id=self.worker.context[0],generation=self.worker.context[1])
        self.remember_frame(self.fix,None,'worker_error')
        self.invalidate_fix(text);self.notify(text)

    def invalidate_fix(self,reason):
        self.fix_live=False
        self.disconnect_recording()
        self.fix=replace(self.fix,valid=False,reason=reason) if self.fix else Fix(reason=reason)
        self.map.update_fix(self.fix)
        self.minimap_overlay.hide()
        self.capture_button.setText('Выключить позиционирование' if self.worker.capture else 'Включить позиционирование')
        self.fix_detail.setText(reason)
        self.navigator.lost();self.tts.stop();self.set_hud({'state':'lost','text':'Позиция потеряна'})
        if self.game_view_available():
            self.set_localization(self.big_localization())
            self.set_hud({'state':'bigmap','text':'Работа с большой картой'})
        else:self.set_localization('Позиция потеряна · подсказки на паузе')

    def remember_frame(self,fix,frame,source):
        self.frame=frame
        self.frame_diagnostic=(frame,{'version':__version__,'source':source,'received_at':time.time(),
            'frame_available':frame is not None,'fix':asdict(fix),
            'capture':deepcopy(self.settings['capture']),'anchor':list(self.settings['anchor']),
            'north_up':self.settings['north_up']})
        if not fix.valid:self.failure_diagnostic=self.frame_diagnostic
        if frame is None:
            self.preview.clear();self.preview.setText('Снимок не получен; можно сохранить диагностику ошибки в JSON')
        else:
            rgb=frame[:,:,::-1].copy();h,w=rgb.shape[:2];image=QImage(rgb.data,w,h,rgb.strides[0],QImage.Format_RGB888).copy()
            self.preview.setPixmap(QPixmap.fromImage(image).scaled(285,170,Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def on_fix(self,fix,frame,live):
        if fix.map_id is not None and ((fix.map_id,fix.generation)!=self.worker.context or fix.map_id!=self.project['map']):return
        if live and not self.worker.capture:return
        if live and fix.session is not None and fix.session!=self.worker.session:return
        now=time.monotonic()
        if not live:self.failure_diagnostic=None
        if fix.valid and live and self.last_accepted and now-self.last_accepted_time<8:
            jump=distance([fix.x,fix.y],self.last_accepted)*(self.project['meters_per_pixel'] or 1)
            if jump>max(80,80*(now-self.last_accepted_time)):
                if self.jump_candidate and distance([fix.x,fix.y],self.jump_candidate)<12:self.jump_count+=1
                else:self.jump_candidate=[fix.x,fix.y];self.jump_count=1
                if self.jump_count<3:fix.valid=False;fix.reason='Резкий скачок позиции, перепроверяем'
            else:self.jump_count=0
        self.fix=fix;self.fix_live=live and fix.valid;self.fix_time=now
        self.remember_frame(fix,frame,'live' if live else 'image')
        self.map.update_fix(fix)
        if fix.valid:
            if live:self.hide_game_map()
            self.set_localization(('Позиция в реальном времени' if live else 'Проверка по картинке')+f' · {fix.confidence:.0%}')
            self.hud_problem=None
            if not live:self.flash_hud('Проверка по картинке успешна')
            heading=f'{fix.heading:.0f}°' if fix.heading is not None else 'неизвестно'
            self.fix_detail.setText(f'Позиция {fix.x:.1f}, {fix.y:.1f}  ·  совпадений {fix.inliers}\nПогрешность {fix.error:.2f}px  ·  курс {heading}')
            if live:self.last_accepted=[fix.x,fix.y];self.last_accepted_time=now
        else:
            self.invalidate_fix(fix.reason);return
        self.record_fix(fix,now,live)
        if self.big_resume and live:self.plan_big_resume([fix.x,fix.y])
        if self.pending_start and live:self.begin_navigation()
        if self.navigator.active and live:
            data=self.navigator.update([fix.x,fix.y],now,self.road_gap([fix.x,fix.y]))
            if data:
                self.set_hud(data)
                if data.get('state') in ('offroute','waiting_road') or (data.get('state')=='wrongway' and data.get('speech')):self.tts.stop()
                if data.get('speech'):
                    receipt=(self.navigator,self.navigator.route,self.navigator.lap,data.get('speech_ids',set()))
                    self.speak(data['speech'],receipt)
                if data.get('state')=='turnaround':self.next_leg()
                if data.get('replan'):self.replan_navigation()
                if data.get('state')=='arrived':self.notify('Вы прибыли')
        self.refresh_hud();self.update_minimap_overlay()

    def replan_navigation(self):
        old=self.navigator;waypoints=list(self.project['waypoints'])
        if old.lap%2:waypoints.reverse()
        remaining=[]
        for p in waypoints:
            projection=nearest_on_route(p,old.route.points)
            if projection and projection[1]>old.progress+3:remaining.append(p)
        goal=self.project['start'] if old.lap%2 else self.project['destination']
        lap,leg=old.lap,old.leg
        route,full=self.route,self.full_route
        if self.plan_route(quiet=True,anchors=[[self.fix.x,self.fix.y],*remaining,goal]):
            self.tts.stop()
            self.start_trip_navigation(lap,leg,preserve_arrival=True)
            self.speak('Маршрут перестроен');self.notify('Пересчитано по прежним правилам и оставшимся путевым точкам')
        else:
            self.route,self.full_route=route,full;self.refresh_map()
            old.defer_replan([self.fix.x,self.fix.y],time.monotonic())
            self.tts.stop();self.set_hud({'state':'waiting_road','text':'Вернитесь на дорогу'});self.notify('Проходимый маршрут пока не найден; поездка сохранена, продолжим после возврата на дорогу')

    def next_leg(self):
        # Rebuild from the original journey endpoints even after an earlier
        # off-route replan shortened the currently followed leg.
        lap,leg=self.navigator.lap,self.navigator.leg
        waypoints=list(self.project['waypoints'])
        if lap%2:waypoints.reverse()
        goal=self.project['start'] if lap%2 else self.project['destination']
        route=self.navigator.route;full=self.full_route.reversed() if self.full_route else route
        if self.plan_route(quiet=True,anchors=[[self.fix.x,self.fix.y],*waypoints,goal]):
            self.start_trip_navigation(lap,leg,preserve_arrival=True)
        else:
            self.route,self.full_route=route,full;self.refresh_map()
            self.navigator.goal=list(goal);self.navigator.return_goal=list(self.project['destination'] if lap%2 else self.project['start'])
            self.navigator.defer_replan([self.fix.x,self.fix.y],time.monotonic())
            self.tts.stop();self.set_hud({'state':'waiting_road','text':'Вернитесь на дорогу'});self.notify('Следующий этап сохранён, продолжим после возврата на дорогу')

    def check_stale(self):
        if self.big_view is not None or self.coordinate_view is not None:
            if not self.game_map_active():self.hide_game_map()
            elif not self.big_view_fresh():
                self.set_localization(self.big_localization());self.update_big_overlay()
        if time.monotonic()-self.fix_time>2.5:
            self.minimap_overlay.hide()
        keep_on_top(self.hud)
        if self.minimap_overlay.isVisible():keep_on_top(self.minimap_overlay)
        if self.big_overlay.isVisible():keep_on_top(self.big_overlay)
        if self.fix_live and time.monotonic()-self.fix_time>2.5:
            self.invalidate_fix('Больше 2,5 с нет точной позиции, ждём повторного определения')
        self.refresh_hud()

    def select_region(self,large=False):
        self.reset_game_map();self.worker.reset_view()
        self.worker.capture=False;self.capture_button.setText('Включить позиционирование');self.stop_navigation(quiet=True)
        if not self.monitors:self.notify('Не найден монитор для захвата');return
        index=max(0,self.screen_combo.currentIndex());monitor=self.monitors[index]
        self.hide();self.hud.hide()
        def grab():
            try:
                import mss
                with mss.MSS() as sct:frame=np.asarray(sct.grab(monitor))[:,:,:3].copy()
                self.show();dialog=RegionDialog(frame,monitor,self,large=large)
                if dialog.exec()==QDialog.Accepted:
                    self.settings['bigmap_capture' if large else 'capture'].update(dialog.region)
                    for key,field in (self.big_fields if large else self.capture_fields).items():field.setValue(dialog.region[key])
                    if large:self.big_enabled.setChecked(True)
                    self.save_settings();self.notify('Область большой карты сохранена; при позиционировании распознавание переключится само' if large else 'Область распознавания сохранена; проверьте метку игрока и включите позиционирование')
            except Exception as error:self.show();self.notify(f'Не удалось захватить монитор: {error}')
        QTimer.singleShot(350,grab)

    def capture_setting(self,key,value):
        self.reset_game_map();self.worker.reset_view()
        self.settings['capture'][key]=value;self.fix_live=False;self.save_settings()

    def anchor_setting(self):self.settings['anchor']=[self.anchor_x.value()/100,self.anchor_y.value()/100];self.fix_live=False;self.save_settings()

    def set_setting(self,key,value):
        self.settings[key]=value
        if key=='voice_rate':self.tts.stop()
        if key=='wrong_way_alert':self.navigator.reset_direction()
        self.save_settings()

    def set_main_topmost(self,value):
        self.settings['main_topmost']=value;self.setWindowFlag(Qt.WindowStaysOnTopHint,value);self.show();self.save_settings()

    def overlay_setting(self,key,value):
        self.settings['minimap_overlay'][key]=value;self.save_settings();self.update_minimap_overlay()

    def update_minimap_overlay(self):
        if not (self.navigator.active and self.fix_live and self.fix and self.fix.valid and self.frame is not None):
            self.minimap_overlay.hide();return
        self.minimap_overlay.update_route(self.route,self.fix,(self.frame.shape[1],self.frame.shape[0]),self.settings['anchor'],self.settings['capture'],full_route=self.full_route,progress=self.navigator.progress)
        if self.minimap_overlay.capture_excluded is False:
            self.overlay_status.setText('Путь показан; при позиционировании область пути исключается, чтобы линия не мешала сопоставлению. Клики проходят насквозь, при потере позиции путь скрывается.')

    def save_capture_diagnostic(self):
        is_failure=self.failure_diagnostic is not None
        snapshot=self.failure_diagnostic or self.frame_diagnostic
        if snapshot is None:self.notify('Сначала включите позиционирование или проверьте по картинке, затем сохраняйте диагностику');return
        frame,metadata=snapshot
        has_frame=frame is not None
        path,_=QFileDialog.getSaveFileName(self,'Сохранить диагностику',
            'диагностика.png' if has_frame else 'диагностика.json','PNG (*.png)' if has_frame else 'JSON (*.json)')
        if not path:return
        try:
            import cv2
            target=Path(path)
            if has_frame:cv2.imencode('.png',frame)[1].tofile(str(target.with_suffix('.png')))
            metadata=dict(metadata,selected='latest_failure' if is_failure else 'latest_frame',
                          saved_at=time.time(),current_fix=asdict(self.fix) if self.fix else None,
                          last_hotkey=self.global_hotkeys.last_event,hotkey_status=self.hotkey_status.text(),
                          last_map_action=self.big_message)
            atomic_json(target.with_suffix('.json'),metadata)
            self.notify('Сохранены последний неудачный кадр PNG и диагностика JSON' if has_frame and is_failure else
                        'Сохранены миникарта PNG и диагностика JSON' if has_frame else 'Снимок не получен; сохранена диагностика ошибки JSON')
        except (OSError,ValueError,cv2.error) as error:self.notify(f'Не удалось сохранить: {error}')

    def hud_setting(self,key,value):self.settings['hud'][key]=value;self.hud.apply_settings();self.save_settings()

    def change_mode(self):
        self.settings['mode']=self.mode.currentData();self.tts.stop();self.save_settings()
        if self.navigator.active:
            self.navigator.cues=cues_for(self.navigator.route,self.project['notes'],self.settings['mode'],self.project['meters_per_pixel'] or 1);self.navigator.spoken.clear()
            self.hud_navigation=None
        self.flash_hud('Стенограмма WRC' if self.settings['mode']=='wrc' else 'Обычная навигация')

    def set_localization(self,text):
        self.fix_label.setText(text);self.hud.set_localization(text)

    def clear_hud_notice(self):
        self.hud_notice_timer.stop();self.hud_notice=None

    def expire_hud_notice(self):
        self.clear_hud_notice();self.refresh_hud()

    def flash_hud(self,text):
        self.set_hud({'state':'notice','text':text})

    def set_hud(self,data):
        state=data.get('state','idle')
        if state=='lost':self.hud_problem=dict(data)
        elif state=='locating':self.hud_problem=None
        elif state=='bigmap':pass  # The current view determines its lifetime.
        elif state in ('navigating','offroute','waiting_road','wrongway','turnaround') and self.navigator.active:
            self.hud_navigation=dict(data)
        elif state=='idle' and data.get('text') in (None,'Ожидание','Ожидание навигации'):pass
        else:
            if state=='capture_off':self.hud_problem=None
            self.hud_notice=dict(data);self.hud_notice_timer.start()
        self.refresh_hud()

    def refresh_hud(self):
        if not hasattr(self,'hud_notice_timer'):return
        big=self.game_view_available()
        recording=self.recorder.active
        paused=recording and (not self.fix_live or big or bool(self.recorder.error))
        activity=('Запись маршрута на паузе' if paused else 'Идёт запись маршрута') if recording else ''
        if big:
            data={'state':'recording_paused','text':activity,'next_text':'Запись продолжится после возврата к миникарте'} if recording and not self.navigator.active else {'state':'bigmap','text':'Работа с большой картой'}
        elif self.hud_problem is not None:data=dict(self.hud_problem)
        elif self.worker.capture and not self.fix_live:data={'state':'locating','text':'Ожидание позиции'}
        elif self.navigator.active:data=dict(self.hud_navigation or {'state':'navigating','text':'Навигация'})
        elif recording:
            data={'state':'recording_paused' if paused else 'recording','text':activity,
                  'next_text':'Включите позиционирование по миникарте' if not self.worker.capture else 'Нажмите «Завершить и добавить в сеть», чтобы закончить запись'}
        elif self.pending_start:data={'state':'locating','text':'Ожидание позиции'}
        else:data={'state':'idle','text':'Ожидание'}
        if self.hud_notice is not None:
            if data['state'] in ('idle','bigmap'):data=dict(self.hud_notice)
            else:data['notice']=self.hud_notice['text']
        if activity and activity!=data['text']:data['activity']=activity
        self.hud.set_data(data,self.settings['mode'],self.project['meters_per_pixel'] is not None)

    def show_hud(self):
        self.refresh_hud();self.hud.show()

    def reset_hud(self):
        rect=QApplication.primaryScreen().availableGeometry();self.settings['hud'].update(x=rect.x()+rect.width()//2-200,y=rect.y()+50)
        for key in ('x','y'):self.hud_fields[key].setValue(self.settings['hud'][key])
        self.hud.apply_settings();self.hud.show();self.save_settings()

    def load_voices(self):
        self.voice_combo.setEnabled(self.settings.get('voice_backend','auto')!='speechkit')
        if self.settings.get('voice_backend','auto')!='system':
            self.voice_status.setText(speechkit_status())
        if self.settings['voice']:self.tts.load_voices()

    def voices_loaded(self,voices):
        self.voices=voices;self.voice_combo.blockSignals(True);self.voice_combo.clear()
        for voice in self.voices:self.voice_combo.addItem(voice['name']+' · '+voice['language'])
        chosen=next((i for i,v in enumerate(self.voices) if v['name']==self.settings['voice_name']),None)
        if chosen is None:chosen=next((i for i,v in enumerate(self.voices) if v['language'].startswith('ru')),0)
        self.voice_combo.setCurrentIndex(chosen);self.voice_combo.blockSignals(False)
        backend=self.settings.get('voice_backend','auto')
        if backend=='speechkit' or (backend=='auto' and speechkit_configured()):return
        if any(v['language'].startswith('ru') for v in self.voices):self.voice_status.setText('Системный голос Windows готов · для голоса Яндекса задайте ключ SpeechKit')
        elif self.voices:self.voice_status.setText('Русский голос Windows не установлен · установите русский языковой пакет с речью или задайте ключ SpeechKit')
        else:self.voice_status.setText('Системные голоса недоступны · задайте ключ SpeechKit')

    def select_voice(self,index):
        if 0<=index<len(self.voices):
            self.tts.stop();self.tts.service.retry_system()
            self.tts.failed_texts.clear()
            self.settings['voice_name']=self.voices[index]['name'];self.save_settings()

    def change_voice_backend(self):
        self.tts.stop();self.tts.service.retry_system()
        self.tts.failed_texts.clear()
        self.settings['voice_backend']=self.voice_backend.currentData();self.save_settings();self.load_voices()

    def change_voice(self,value):
        self.settings['voice']=value;self.tts.stop();self.save_settings()
        if value:self.load_voices()

    def speech_result(self,receipt,completed):
        if receipt is None:return
        navigator,route,lap,ids=receipt
        if navigator is self.navigator and navigator.route is route and navigator.lap==lap:
            navigator.pending.difference_update(ids)
            if completed:navigator.spoken.update(ids)

    def speak(self,text,receipt=None):
        if self.settings['voice']:return self.tts.enqueue(text,receipt)
        # Muting intentionally consumes the current cue; it is not a cancelled playback.
        self.speech_result(receipt,True)
        return False

    def test_voice(self):
        self.tts.failed_texts.clear();self.tts.service.retry_system()
        self.tts.stop();self.tts.enqueue('Двигайтесь по текущей дороге восемьсот метров. Через сто метров на перекрёстке прямо. На следующем перекрёстке направо.' if self.settings['mode']=='normal' else 'Сто метров, левый три, затягивается, в правый шесть. Пятьдесят метров, тормоз, в левый девяносто, не резать.')

    def test_image(self):
        path,_=QFileDialog.getOpenFileName(self,'Проверка позиционирования по снимку миникарты','','Снимки карты (*.png *.jpg *.jpeg *.bmp)')
        if path:
            self.failure_diagnostic=None
            self.worker.capture=False;self.capture_button.setText('Включить позиционирование');self.stop_navigation(quiet=True);self.worker.request_sample(path);self.notify('Проверяем картинку; для навигации снова включите позиционирование')

    def update_scale_label(self):
        scale=self.project['meters_per_pixel'];text=f'{scale:.4f} м / пиксель карты' if scale else 'Не откалибровано · используются единицы карты'
        self.scale_label.setText(text+'\n'+self.project.get('calibration',{}).get('status',''))

    def set_scale(self):
        value,ok=QInputDialog.getDouble(self,'Масштаб','Метров в одном пикселе карты',self.project['meters_per_pixel'] or 1,.01,1000,4)
        if ok:self.snapshot();self.project['meters_per_pixel']=value;self.project['calibration']={'source':'Ввод пользователем','status':'Откалибровано пользователем'};self.changed();self.update_scale_label()

    def help(self):
        QMessageBox.information(self,'Справка',
            '1. Оставьте игру в полноэкранном режиме, миникарта — север сверху. Оверлей зависит от оптимизации полноэкранного режима Windows; если его перекрывает, попробуйте окно без рамки.\n'
            '2. Выберите вверху текущую карту игры, выделите миникарту, настройте метку игрока (по умолчанию — центр области) и включите позиционирование.\n'
            '3. Укажите цель на карте; выделите рамкой опасные зоны — маршрут их обойдёт. Путевые точки задают обязательные места проезда.\n'
            '4. Старт задаётся на карте, перетаскиванием или по текущей позиции; в челноке это конец обратного пути. Если не задан, при запуске берётся текущая позиция, а смена цели в пути его не меняет. На миникарте бледная линия — весь маршрут, яркая — оставшаяся часть.\n'
            '5. В режиме «Обзор / выбор» щелчок выбирает дорогу, точки можно тянуть; двойной щелчок — свойства, ПКМ — правка или удаление. При рисовании Enter — готово, Esc — отмена, Ctrl+Z — отменить. WRC 1 — медленный поворот, 6 — быстрый; ручная стенограмма важнее расчёта.\n\n'
            '6. Типы дорог: главная, второстепенная, бездорожье; Backspace — шаг назад. Дороги можно импортировать и экспортировать отдельно.\n'
            '7. Для сохранённого маршрута можно включить «Привязать к дорогам» и грубо расставить точки — появится превью по дорогам. Без привязки путь сохраняется как нарисован, а дороги можно достроить. При импорте опции подтверждаются так же; без достройки маршрут тоже работает, но только сам. Экспорт хранит путь, типы, опорные точки, опции и стенограмму; маршрут можно загрузить в обратную сторону. Опасные зоны и правила дорог действуют и здесь.\n'
            '8. Обычный режим подсказывает манёвры на перекрёстках и расстояния; WRC — сложность поворотов, «девяносто», ручные «тормоз» и т. п., с тремя следующими командами. Упреждение настраивается для режимов отдельно, список подсказок — на вкладке «Навигация».\n\n'
            '9. OZETI, BAKURANI и ZESTAFONA хранят дороги, маршруты, опасные зоны, стенограмму и масштаб отдельно. Смена карты сохраняет настройки и останавливает навигацию; продолжите, снова включив позиционирование. Импорт полных настроек переключает на их карту, а дороги и маршруты объединяются только на той же карте.\n\n'
            'Дороги участвуют в построении по своему типу, подтверждать их не нужно; на карте дороги пунктиром, маршрут — сплошной линией. «Сбросить маршрут» останавливает навигацию, убирает старт, цель, путевые точки и линию маршрута и выходит из сохранённого маршрута; видимость значка, дороги, маршруты и опасные зоны остаются. Поездку можно вернуть отменой, навигацию затем запускают вручную. Путевые точки и опасные зоны очищаются отдельно. Концы маршрута привязываются к дорогам, без подводки по неизвестной местности. По картинке нельзя распознать мины, текущие завалы или проходимость бездорожья.\n'
            'Программа читает только выбранную область экрана, не трогает память игры и не управляет техникой. Допустимость сторонних инструментов определяют правила игры.\n\n'
            f'Настройки сохраняются автоматически в: {user_dir()}\nЭкспорт в JSON — для резервной копии или обмена настройками карты.')

    def closeEvent(self,event):
        self.record_timer.stop();self.disconnect_recording();self.save_recording()
        self.cursor_timer.stop();self.global_hotkeys.close();self.big_overlay.close()
        self.minimap_overlay.close()
        self.hud_notice_timer.stop();self.watchdog.stop();self.autosave.stop();self.tts.close();self.hud.close();self.worker.shutdown();self.save_project();self.save_settings();event.accept()


def run():
    app=QApplication.instance() or QApplication([])
    app.setApplicationName('WardogsNavigator');app.setOrganizationName('WardogsNavigator')
    app.setStyle('Fusion');app.setStyleSheet(STYLE)
    window=MainWindow();window.show()
    return app.exec()
