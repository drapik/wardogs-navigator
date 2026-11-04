import time
import numpy as np
import pytest
from PySide6.QtTest import QTest
from test_bigmap import app,window,mini,large
from wardogs_nav.model import load_settings
from wardogs_nav.routing import plan
from wardogs_nav.vision import feature_mask


def pixels(overlay):
    image=overlay.surface
    return np.frombuffer(image.constBits(),np.uint8).reshape(image.height(),image.width(),4).copy()


def test_bigmap_roads_toggle_persists_and_keeps_route_on_top(window):
    mini(window);view=large(window)
    window.project.update(roads=[dict(id='network',kind='major',name='Дорога',points=[[10,120],[390,120]])],
                          start=None,destination=None,waypoints=[],avoid=[],notes=[],destinations=[])
    overlay=window.big_overlay;overlay.capture_excluded=False
    overlay.update_map(view,window.project)
    assert not window.big_roads.isChecked() and not pixels(overlay)[237:244,20:780,3].any()
    session=window.worker.session
    window.map.show_roads=False  # This switch is independent of desktop-map visibility.
    window.big_roads.setChecked(True)
    road=pixels(overlay)
    assert road[237:244,20:780,3].any() and (road[240,20:780,3]==0).any()
    assert load_settings()['bigmap']['show_roads'] and window.worker.session==session
    mask=feature_mask(np.zeros((800,800,3),np.uint8),minimap=False,path_mask=window.worker.map_mask)
    assert np.all(mask[road[:,:,3]>0]==0)
    window.route=plan(window.project['roads'],[[30,120],[350,120]],['major'])
    window.update_big_overlay()
    assert tuple(pixels(overlay)[240,300])==(103,237,195,255)
    window.big_roads.setChecked(False)
    assert not load_settings()['bigmap']['show_roads']
    assert not pixels(overlay)[237:244,20:50,3].any()
    assert tuple(pixels(overlay)[240,300])==(103,237,195,255)


def test_recording_replaces_old_status_and_recovers_after_loss(window):
    mini(window);assert window.hud.data['text']=='Ожидание'
    window.stop_navigation();assert window.hud.data['text']=='Навигация остановлена'
    window.start_recording()
    assert window.hud.data['text']=='Идёт запись маршрута' and window.hud.isVisible()
    window.expire_hud_notice()  # An earlier completion must never end this operation.
    assert window.hud.data['text']=='Идёт запись маршрута'
    window.invalidate_fix('Временно закрыто')
    assert window.hud.data['state']=='lost' and window.hud.data['activity']=='Запись маршрута на паузе'
    window.show_hud();assert window.hud.data['state']=='lost'
    mini(window);assert window.hud.data['text']=='Идёт запись маршрута'


def test_recording_finished_message_expires_with_real_timer(window):
    mini(window);window.start_recording()
    for i,x in enumerate((100,120,140,160)):window.recorder.feed([x,500],float(i),False)
    window.finish_recording()
    assert window.hud.data['text']=='Запись завершена' and window.hud_notice_timer.interval()==2000
    for _ in range(3):mini(window)
    assert window.hud.data['text']=='Запись завершена'  # New fixes must not erase the notice.
    QTest.qWait(2150)
    assert not window.hud_notice_timer.isActive() and window.hud.data['text']=='Ожидание'


@pytest.mark.parametrize('mode',['normal','wrc'])
def test_navigation_and_recording_keep_guidance_and_show_activity(window,mode):
    mini(window);window.settings['mode']=mode;window.project['destination']=[300,100]
    window.begin_navigation()
    data=window.navigator.update([100,100],time.monotonic(),0)
    window.set_hud(data);window.start_recording()
    assert window.hud.data['text']==data['text'] and window.hud.data['activity']=='Идёт запись маршрута'
    if mode=='wrc':assert window.hud.data['upcoming']==data['upcoming']
    window.finish_recording()
    assert window.hud.data['text']==data['text'] and 'Запись остановлена' in window.hud.data['notice']
    window.expire_hud_notice()
    assert window.navigator.active and window.hud.data['text']==data['text']
    assert 'activity' not in window.hud.data and 'notice' not in window.hud.data


def test_record_completion_does_not_dismiss_persistent_position_loss(window):
    mini(window);window.start_recording();window.invalidate_fix('Долгая потеря')
    window.finish_recording()
    assert window.hud.data['state']=='lost' and 'Запись остановлена' in window.hud.data['notice']
    window.expire_hud_notice();assert window.hud.data['state']=='lost'
    mini(window);assert window.hud.data['text']=='Ожидание'


def test_offroad_warning_survives_notices_while_recording(window):
    mini(window);window.project['destination']=[300,100];window.begin_navigation();window.start_recording()
    window.set_hud({'state':'waiting_road','text':'Вернитесь на дорогу'})
    window.flash_hud('Тестовое действие выполнено');window.expire_hud_notice()
    assert window.hud.data['text']=='Вернитесь на дорогу' and window.hud.data['activity']=='Идёт запись маршрута'


def test_new_operation_and_bigmap_are_not_replaced_by_idle_on_timeout(window):
    mini(window);window.stop_navigation();window.start_recording();large(window)
    assert window.hud.data['text']=='Запись маршрута на паузе'
    window.expire_hud_notice();assert window.hud.data['text']=='Запись маршрута на паузе'
    mini(window);assert window.hud.data['text']=='Идёт запись маршрута'


def test_capture_off_goes_idle_but_waiting_fix_and_paused_recording_persist(window):
    mini(window);window.toggle_capture();assert window.hud.data['text']=='Позиционирование выключено'
    window.expire_hud_notice();assert window.hud.data['text']=='Ожидание'
    window.toggle_capture();window.expire_hud_notice();assert window.hud.data['state']=='locating'
    mini(window);window.start_recording();window.toggle_capture();window.expire_hud_notice()
    assert window.hud.data['text']=='Запись маршрута на паузе' and not window.worker.capture


def test_map_change_clears_previous_localization_loss(window):
    mini(window);window.invalidate_fix('Потеря позиции на старой карте')
    assert window.switch_map('bakurani')
    assert window.hud.data['text']=='Ожидание'


def test_arrival_and_clear_return_to_current_state_after_notice(window):
    mini(window);window.set_hud({'state':'arrived','text':'Вы прибыли','remaining':0})
    assert window.hud.data['state']=='arrived'
    mini(window);assert window.hud.data['state']=='arrived'
    window.expire_hud_notice();assert window.hud.data['text']=='Ожидание'
    window.clear_current_route();assert window.hud.data['text']=='Маршрут сброшен'
    window.expire_hud_notice();assert window.hud.data['text']=='Ожидание'


def test_starting_coordinate_navigation_keeps_target_confirmation(window,monkeypatch):
    from wardogs_nav.coordinates import map_to_game
    window.worker.capture=False
    monkeypatch.setattr(window.worker,'isRunning',lambda:True)
    point=map_to_game(window.project['map'],[300,100])
    window.coordinate_input.setText(f'x{point[0]}, y{point[1]}')
    window.coordinate_action.setCurrentIndex(window.coordinate_action.findData('navigate'))
    window.apply_coordinates()
    assert window.pending_start and window.hud.data['state']=='locating'
    assert window.hud.data['notice']=='Цель задана'
    mini(window)
    assert window.navigator.active and window.hud.data['notice']=='Цель задана'
