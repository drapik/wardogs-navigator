import threading
import time
from collections import deque
import numpy as np
from PySide6.QtCore import QThread, Signal
from .vision import Locator, read_image, Fix, MapViewFix
from .hotkeys import foreground_window
from .coordinate_reader import CoordinateReader


class CaptureWorker(QThread):
    result=Signal(object,object,bool)
    ready=Signal(str,int)
    failed=Signal(str,str,int)
    map_result=Signal(object,object,object)
    action_failed=Signal(str)

    def __init__(self, settings):
        super().__init__()
        self.settings=settings
        self.capture=False
        self.sample=None
        self.path_mask=None
        self.map_mask=None
        self.mode='idle'
        self.session=0
        self.actions=deque()
        self.action_lock=threading.Lock()
        self.target_window=0
        self.context=(settings.get('map_id','ozeti'),0)
        self.wake=threading.Event()
        self.coordinates=CoordinateReader()

    def request_map(self,map_id):
        self.reset_view()
        self.capture=False;self.sample=None;self.path_mask=None
        self.context=(map_id,self.context[1]+1)
        self.wake.set()

    def reset_view(self):
        self.session+=1;self.mode='idle';self.map_mask=None;self.target_window=0
        with self.action_lock:self.actions.clear()
        self.wake.set()

    @property
    def map_action(self):
        with self.action_lock:return self.actions[0] if self.actions else None

    def request_action(self,action,point,foreground):
        with self.action_lock:
            full=len(self.actions)>=8
            if not full:self.actions.append((self.session,action,point,foreground,time.monotonic()))
        if full:
            self.action_failed.emit('Слишком много действий ждут проверки, попробуйте позже');return False
        self.wake.set()
        return True

    def take_actions(self):
        with self.action_lock:
            actions=list(self.actions);self.actions.clear()
        return actions

    def retry_actions(self,actions,foreground):
        retry=[]
        for action in actions:
            if action[0]==self.session and action[3]==foreground and time.monotonic()-action[4]<6:retry.append(action)
            else:self.action_failed.emit('Не удалось проверить позицию курсора, действие отменено. Дождитесь распознавания большой карты и повторите')
        with self.action_lock:
            pending=retry+list(self.actions);self.actions=deque(pending[:8])
        if len(pending)>8:self.action_failed.emit('Слишком много действий ждут проверки, последнее отменено. Повторите позже')

    def request_sample(self,path):
        self.reset_view()
        self.sample=(self.context,str(path))
        self.wake.set()

    def run(self):
        grabber=None;locator=None;loaded=None;view_session=None
        while not self.isInterruptionRequested():
            context=self.context
            if loaded!=context or (locator is None and self.capture):
                # Release the previous FLANN index before loading a new map;
                # laptop memory use stays tied to one active map.
                locator=None
                try:
                    locator=Locator.from_assets(context[0])
                    if context!=self.context:continue
                    loaded=context;self.ready.emit(*context)
                except Exception as error:
                    if context!=self.context:continue
                    loaded=context;self.capture=False
                    self.failed.emit(f'Ошибка инициализации признаков карты: {error}',*context)
            if context!=self.context:continue
            sample=self.sample
            if sample and sample[0]==context:self.sample=None
            elif sample:continue
            live=self.capture and sample is None
            if locator is not None and (sample or live):
                session=self.session
                if view_session!=session:
                    locator.map_reference=None;view_session=session;self.coordinates.reset()
                actions=self.take_actions()
                frame=None
                try:
                    if sample:
                        frame=read_image(sample[1])
                    else:
                        region=dict(self.settings['capture'])
                        if region['width'] < 60 or region['height'] < 60:
                            if context!=self.context or not self.capture:continue
                            self.capture=False
                            self.failed.emit('Область захвата должна быть не меньше 60 × 60 пикселей. Исправьте и снова включите определение позиции',*context)
                            continue
                        if grabber is None:
                            import mss
                            grabber=mss.MSS()
                        frame=np.asarray(grabber.grab(region))[:,:,:3].copy()
                    fix=locator.locate(frame,tuple(self.settings['anchor']),self.settings['north_up'],None if sample else self.path_mask)
                except Exception as error:
                    if context!=self.context:continue
                    locator.last=None
                    fix=Fix(reason=f'Ошибка захвата или определения позиции: {error}')
                    if not sample and frame is None and grabber is not None:
                        try:grabber.close()
                        except Exception:pass
                        grabber=None
                # A failed frame is a result too. Keep its image (or explicit
                # absence) paired with the error, and retry at the normal pace.
                if context!=self.context or session!=self.session or (live and not self.capture):continue
                fix.map_id,fix.generation=context
                fix.session=session
                if live and (fix.valid or self.mode!='big'):self.mode='mini' if fix.valid else 'searching'
                self.result.emit(fix,frame,live)
                if live and fix.valid:
                    locator.map_reference=None
                    self.coordinates.reset()
                    if actions:self.action_failed.emit('Возврат к мини-карте, незавершённые действия на большой карте отменены')
                    # No big-map capture, matching, foreground or cursor queries.
                elif live and self.settings.get('bigmap',{}).get('enabled'):
                    view=MapViewFix();big_frame=None
                    region=dict(self.settings['bigmap_capture'])
                    foreground=foreground_window();captured=time.monotonic()
                    try:
                        if self.target_window and foreground!=self.target_window:
                            view.reason='Игра не на переднем плане'
                        elif region['width']<60 or region['height']<60:
                            view.reason='Область большой карты должна быть не меньше 60 × 60 пикселей'
                        else:
                            if grabber is None:
                                import mss
                                grabber=mss.MSS()
                            big_frame=np.asarray(grabber.grab(region))[:,:,:3].copy()
                            view=locator.locate_map(big_frame,self.map_mask)
                            if not view.valid and self.settings['bigmap'].get('coordinates',True):
                                view.coordinate=self.coordinates.read(big_frame,context[0],(context,session,foreground,tuple(region.values())))
                                if self.coordinates.error:view.reason=self.coordinates.error
                            else:self.coordinates.reset()
                            if foreground_window()!=foreground:
                                view.valid=False;view.coordinate=None;view.reason='Активное окно сменилось'
                    except Exception as error:
                        view=MapViewFix(reason=f'Ошибка захвата или распознавания большой карты: {error}')
                    if context!=self.context or session!=self.session or not self.capture:continue
                    if region!=self.settings['bigmap_capture'] or not self.settings['bigmap']['enabled']:continue
                    view.map_id,view.generation=context
                    view.session=session;view.captured_at=captured;view.region=region;view.foreground=foreground
                    self.mode='big' if view.valid or view.coordinate else 'lost'
                    # Retain quick consecutive presses and retry transient failures.
                    pending=[a for a in actions if a[0]==session and a[3]==foreground and captured-a[4]<6]
                    if len(pending)<len(actions):self.action_failed.emit('Время действия на большой карте истекло или окно сменилось, повторите')
                    if not view.valid and not view.coordinate:
                        self.retry_actions(pending,foreground);pending=[]
                    self.map_result.emit(view,big_frame,pending)
            self.wake.wait(.2 if self.mode=='big' else max(.2,self.settings['interval_ms']/1000))
            self.wake.clear()
        if grabber:
            grabber.close()
        self.coordinates.cleanup()

    def shutdown(self):
        self.capture=False
        self.requestInterruption()
        self.coordinates.close()
        self.wake.set()
        self.wait()
        self.coordinates.cleanup()
