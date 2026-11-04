"""Map-local recording controls and persistence for the navigation window."""
import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox,QFormLayout,QMessageBox
from .dialogs import number
from .maps import map_info
from .model import user_dir,atomic_json
from .recording import RoadRecorder,add_recorded_roads
from .ru import plural


class RecordingController:
    def init_recording(self):
        self.settings['recording_minimum']=max(1,min(100,int(self.settings['recording_minimum'])))
        self.load_recording()
        self.record_timer=QTimer(self);self.record_timer.setInterval(2000)
        self.record_timer.timeout.connect(self.save_recording);self.record_timer.start()

    def recording_path(self):return user_dir()/'recordings'/(self.project['map']+'.json')

    def load_recording(self):
        state=None;self.record_load_error=''
        path=self.recording_path()
        if path.exists():
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                if data.get('map')!=self.project['map'] or data.get('schema')!=1:raise ValueError('Карта или версия не совпадает')
                state=data['state'];info=map_info(self.project['map'])
                if not isinstance(state['active'],bool) or len(state['units'])>20000:raise ValueError('Некорректные данные записи')
                paths=list(state['manual'])
                for unit in state['units']:
                    if not isinstance(unit['id'],str) or not 1<=unit['count']<=100 or not isinstance(unit['added'],bool):raise ValueError('Некорректное число проездов')
                    paths.append(unit['points'])
                for points in paths:
                    for p in points:
                        if len(p)!=2 or not 0<=p[0]<=info['width'] or not 0<=p[1]<=info['height']:raise ValueError('Некорректные координаты записи')
            except (OSError,ValueError,KeyError,TypeError) as error:
                state=None;self.record_load_error=f'Запись не прочитана, исходный файл сохранён: {error}'
        self.recorder=RoadRecorder(state,self.project['meters_per_pixel'] or 1)
        self.recorder.set_roads(self.project['roads'])
        if hasattr(self,'record_status'):self.update_recording_status()

    def build_recording(self,layout):
        group=self.group('Запись маршрута поездки',layout)
        self.record_start=self.button('Начать запись',self.start_recording)
        self.record_finish=self.button('Завершить и добавить в сеть',self.finish_recording)
        group.addWidget(self.row(self.record_start,self.record_finish))
        self.auto_record=QCheckBox('Считать проезды и строить дороги автоматически')
        self.auto_record.setChecked(self.settings['recording_auto'])
        self.auto_record.toggled.connect(self.toggle_auto_recording);group.addWidget(self.auto_record)
        form=QFormLayout();self.record_minimum=number(self.settings['recording_minimum'],1,100)
        self.record_minimum.valueChanged.connect(self.set_recording_minimum)
        form.addRow('Минимум проездов',self.record_minimum);group.addLayout(form)
        group.addWidget(self.button('Сбросить счётчики этой карты',self.clear_recording_counts))
        self.record_status=self.text('',True);group.addWidget(self.record_status)
        group.addWidget(self.text('Пишется только позиция на мини-карте, навигацию запускать не нужно. Новые дороги по умолчанию — бездорожье, совпадающие участки пропускаются, на пересечениях дорога делится. Стоянка не считается, проезд в обратную сторону тоже засчитывается; при потере позиции или открытой большой карте запись прерывается и продолжается после восстановления.',True))
        self.update_recording_status()

    def update_recording_status(self):
        if not hasattr(self,'record_status'):return
        self.record_start.setEnabled(not self.recorder.active and not self.record_load_error)
        self.record_finish.setEnabled(self.recorder.active and not self.record_load_error)
        if self.record_load_error:self.record_status.setText(self.record_load_error);return
        count=sum(len(p) for p in self.recorder.state['manual'])
        text=f'Ручная запись · {count} {plural(count,"точка","точки","точек")}' if self.recorder.active else 'Ручная запись не начата'
        if self.recorder.active and not self.fix_live:text+=' · ожидание позиции'
        pending=[u for u in self.recorder.state['units'] if not u['added']]
        if self.settings['recording_auto']:
            text+=f"\nАвтозапись включена · максимум у кандидатов {max((u['count'] for u in pending),default=0)} / {self.settings['recording_minimum']}"
        else:text+='\nАвтозапись выключена · счётчики сохранены'
        self.record_status.setText(self.recorder.error or text)
        if hasattr(self,'map'):self.map.update_recording(self.recorder.state['manual'])
        self.refresh_hud()

    def start_recording(self):
        self.clear_hud_notice();self.recorder.start();self.update_recording_status();self.save_recording();self.hud.show()
        self.notify('Запись маршрута начата. Включите определение позиции по мини-карте, по завершении дороги добавятся в сеть')

    def finish_recording(self):
        self.disconnect_recording()
        count=self.apply_recorded_roads(self.recorder.stop(),'manual')
        self.save_recording();self.update_recording_status()
        self.flash_hud('Запись завершена' if count else 'Запись остановлена, новых участков нет')
        self.notify(f'Добавлено дорог по бездорожью: {count}. Их можно изменить или отменить' if count else 'Запись окончена: нет достаточно длинных новых участков, совпадения пропущены')

    def toggle_auto_recording(self,enabled):
        self.disconnect_recording()
        self.settings['recording_auto']=enabled;self.save_settings();self.update_recording_status()

    def set_recording_minimum(self,value):
        self.settings['recording_minimum']=int(value);self.save_settings()
        self.promote_recording();self.update_recording_status()

    def clear_recording_counts(self):
        if QMessageBox.question(self,'Сброс счётчиков','Сбросить счётчики автозаписи этой карты? Дороги в сети и ручные записи сохранятся.',QMessageBox.Yes|QMessageBox.No,QMessageBox.No)!=QMessageBox.Yes:return
        self.recorder.clear_counts();self.save_recording();self.update_recording_status()

    def apply_recorded_roads(self,traces,source):
        if not traces:return 0
        roads,count=add_recorded_roads(self.project['roads'],traces,self.project['meters_per_pixel'] or 1,source)
        if count:
            self.snapshot();self.project['roads']=roads;self.recorder.set_roads(roads)
            # Appending roads must not cancel, reset, or announce the active trip.
            self.refresh_lists();self.refresh_map();self.autosave.start()
        return count

    def promote_recording(self):
        if not self.settings['recording_auto'] or self.record_load_error:return
        ready=self.recorder.ready(self.settings['recording_minimum'])
        if ready:
            count=self.apply_recorded_roads([u['points'] for u in ready],'auto')
            self.recorder.promoted(ready)
            if count:self.notify(f'Автозапись обновила дороги по бездорожью: {count}. Их можно изменить или отменить')

    def record_fix(self,fix,now,live):
        if self.record_load_error:return
        if live and fix.valid:
            self.recorder.scale=self.project['meters_per_pixel'] or 1
            self.recorder.feed([fix.x,fix.y],now,self.settings['recording_auto'])
            self.promote_recording();self.update_recording_status()
        else:self.disconnect_recording()

    def disconnect_recording(self):
        if not hasattr(self,'recorder'):return
        self.recorder.disconnect();self.promote_recording();self.update_recording_status()

    def save_recording(self):
        if not self.recorder.dirty or self.record_load_error:return
        try:
            atomic_json(self.recording_path(),dict(schema=1,map=self.project['map'],state=self.recorder.state))
            self.recorder.dirty=False
        except (OSError,ValueError) as error:self.notify(f'Не удалось сохранить запись поездки: {error}')
