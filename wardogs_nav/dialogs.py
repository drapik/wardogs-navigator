from PySide6.QtCore import Qt, Signal, QRect, QPoint
from PySide6.QtGui import QPixmap, QImage, QPainter, QColor, QPen
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QFormLayout,QLineEdit,QComboBox,QCheckBox,
    QDialogButtonBox,QDoubleSpinBox,QSpinBox,QLabel,QWidget,QPushButton,QHBoxLayout)
from .model import KINDS, NOTE_TYPES, MODIFIERS
from .ru import count
from .navigation import Cue, cue_text


def number(value, low, high, decimals=0):
    widget=QDoubleSpinBox() if decimals else QSpinBox()
    widget.setRange(low,high)
    if decimals:
        widget.setDecimals(decimals)
    widget.setValue(value)
    return widget


class RoadDialog(QDialog):
    def __init__(self, road, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Свойства дороги');self.setMinimumWidth(440)
        layout=QVBoxLayout(self);form=QFormLayout()
        self.name=QLineEdit(road.get('name','Новая дорога'))
        self.kind=QComboBox()
        for value,label in KINDS.items():self.kind.addItem(label,value)
        self.kind.setCurrentIndex(self.kind.findData(road.get('kind','minor')))
        self.note=QLineEdit(road.get('note',''))
        form.addRow('Название',self.name);form.addRow('Класс',self.kind);form.addRow('Заметка',self.note)
        layout.addLayout(form)
        self.bridge=QCheckBox('Мост (отдельный уровень от наземных дорог)');self.bridge.setChecked(road.get('bridge',False));layout.addWidget(self.bridge)
        self.bridge_start=QCheckBox('Начало на земле (реальный въезд на мост / съезд)');self.bridge_start.setChecked(road.get('bridge_start',False));layout.addWidget(self.bridge_start)
        self.bridge_end=QCheckBox('Конец на земле (реальный въезд на мост / съезд)');self.bridge_end.setChecked(road.get('bridge_end',False));layout.addWidget(self.bridge_end)
        points=road.get('points',[])
        if points:
            label=QLabel(f'Начало дороги ({points[0][0]:.1f}, {points[0][1]:.1f}) → конец ({points[-1][0]:.1f}, {points[-1][1]:.1f})');label.setWordWrap(True);layout.addWidget(label)
        def bridge_options(value):
            self.bridge_start.setEnabled(value);self.bridge_end.setEnabled(value)
        self.bridge.toggled.connect(bridge_options);bridge_options(self.bridge.isChecked())
        label=QLabel('В местах пересечения мост не соединяется с землёй. Отмечайте «на земле» только у реальных въездов на мост; если мост разбит на несколько дорог, промежуточные стыки не отмечайте. Класс дороги по-прежнему учитывает предпочтения, через опасные зоны проезда нет.');label.setWordWrap(True);layout.addWidget(label)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def values(self):
        return dict(name=self.name.text().strip() or 'Дорога без названия',kind=self.kind.currentData(),confirmed=True,note=self.note.text(),
                    bridge=self.bridge.isChecked(),bridge_start=self.bridge.isChecked() and self.bridge_start.isChecked(),
                    bridge_end=self.bridge.isChecked() and self.bridge_end.isChecked())


class FavoriteImportDialog(QDialog):
    def __init__(self,routes,parent=None):
        super().__init__(parent);self.setWindowTitle('Импорт избранных маршрутов');self.setMinimumWidth(470)
        layout=QVBoxLayout(self)
        label=QLabel(f'Будет добавлено: {count(len(routes),"маршрут","маршрута","маршрутов")}. Текущее избранное, цели и опасные зоны сохранятся.');label.setWordWrap(True);layout.addWidget(label)
        self.snap=QCheckBox('Привязать к существующим дорогам (пересчитать маршрут)');layout.addWidget(self.snap)
        description=QLabel('Если включено, между опорными точками маршрут прокладывается по текущей сети дорог с учётом правил и опасных зон; при ошибке импорт не выполняется. Если выключено, сохраняется полный путь из файла.')
        description.setWordWrap(True);layout.addWidget(description)
        self.road_action=QComboBox()
        self.road_action.addItem('Выберите, создавать ли дороги по маршруту…','choose')
        self.road_action.addItem('Да: добавить недостающие участки в общую сеть дорог',True)
        self.road_action.addItem('Нет: только для навигации по избранному, без добавления в общую сеть',False)
        if routes and all('build_roads' in saved for saved in routes):
            builds=sum(saved['build_roads'] for saved in routes)
            self.road_action.addItem(f'Как в файле (с дорогами: {builds}, только избранное: {len(routes)-builds})','stored')
            self.road_action.setCurrentIndex(3)
        layout.addWidget(QLabel('Без привязки: создавать ли дороги по маршруту?'));layout.addWidget(self.road_action)
        note=QLabel('Без создания дорог по исходному маршруту тоже можно ехать: недостающие участки используются только в этой поездке по избранному и всё равно подчиняются классам дорог и опасным зонам. Созданные дороги сразу доступны для планирования; импорт можно отменить.')
        note.setWordWrap(True);layout.addWidget(note)
        self.buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Ok).setText('Импортировать')
        self.buttons.button(QDialogButtonBox.Cancel).setText('Отмена')
        self.buttons.accepted.connect(self.accept);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.snap.toggled.connect(self.update_options);self.road_action.currentIndexChanged.connect(self.update_options);self.update_options()

    def update_options(self,*_):
        self.road_action.setEnabled(not self.snap.isChecked())
        self.buttons.button(QDialogButtonBox.Ok).setEnabled(self.snap.isChecked() or self.road_action.currentData()!='choose')

    def options(self):
        action=self.road_action.currentData()
        return dict(snap_to_roads=self.snap.isChecked(),build_roads=None if action=='stored' else action if isinstance(action,bool) else False)


class NoteDialog(QDialog):
    def __init__(self,note=None,parent=None):
        super().__init__(parent)
        self.setWindowTitle('Отметка стенограммы WRC');self.setMinimumWidth(470)
        note=note or {};layout=QVBoxLayout(self);form=QFormLayout()
        self.kind=QComboBox()
        for value,label in NOTE_TYPES.items():self.kind.addItem(label,value)
        self.kind.setCurrentIndex(max(0,self.kind.findData(note.get('type','left'))))
        self.grade=number(note.get('grade',4),1,6)
        self.direction=QComboBox()
        for label,value in [('Оба направления (в обратном лево/право меняются)','both'),('Только прямое','forward'),('Только обратное','reverse')]:self.direction.addItem(label,value)
        self.direction.setCurrentIndex(max(0,self.direction.findData(note.get('direction','both'))))
        self.bearing=number(note.get('bearing',0),0,359)
        self.lead=number(note.get('lead_m',0),0,2000)
        self.text=QLineEdit(note.get('text',''));self.text.setPlaceholderText('Пусто — стандартная подсказка вроде «Левый 4»')
        self.reverse=QLineEdit(note.get('reverse_text',''));self.reverse.setPlaceholderText('Текст для обратного направления; пусто — автоматически по типу')
        form.addRow('Тип',self.kind);form.addRow('Степень поворота (1 медленный / 6 быстрый)',self.grade);form.addRow('Направление',self.direction)
        form.addRow('Курс въезда в прямом направлении (север 0 / восток 90)',self.bearing);form.addRow('Упреждение (м; 0 — общее значение)',self.lead)
        form.addRow('Текст в прямом направлении',self.text);form.addRow('Текст в обратном направлении',self.reverse);layout.addLayout(form)
        self.modifiers={}
        modifier_row=QHBoxLayout()
        for key,label in MODIFIERS.items():
            box=QCheckBox(label);box.setChecked(key in note.get('modifiers',[]));self.modifiers[key]=box
            box.toggled.connect(self.update_preview);modifier_row.addWidget(box)
        layout.addLayout(modifier_row)
        self.preview=QLabel();self.preview.setWordWrap(True);layout.addWidget(self.preview)
        for control in (self.kind,self.grade,self.text):
            signal=control.currentIndexChanged if isinstance(control,QComboBox) else control.valueChanged if hasattr(control,'valueChanged') else control.textChanged
            signal.connect(self.update_preview)
        self.update_preview()
        if parent is not None and hasattr(parent,'speak'):
            audition=QPushButton('Прослушать подсказку');audition.clicked.connect(lambda:parent.speak(self.preview.text()));layout.addWidget(audition)
        hint=QLabel('Сначала поставьте отметку на маршрут движения. Направление определяется курсом машины при въезде в точку; упреждение в секундах складывается с упреждением по расстоянию.');hint.setWordWrap(True);layout.addWidget(hint)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def values(self):
        return dict(type=self.kind.currentData(),grade=self.grade.value(),direction=self.direction.currentData(),bearing=self.bearing.value(),lead_m=self.lead.value(),text=self.text.text().strip(),reverse_text=self.reverse.text().strip(),modifiers=[k for k,w in self.modifiers.items() if w.isChecked()])

    def update_preview(self,*_):
        if not hasattr(self,'preview'):return
        self.grade.setEnabled(self.kind.currentData() in ('left','right'))
        cue=Cue('preview',0,self.kind.currentData(),self.grade.value(),self.text.text().strip(),
                modifiers=[k for k,w in self.modifiers.items() if w.isChecked()])
        self.preview.setText(cue_text(cue,'wrc',spoken=True))


class RegionCanvas(QWidget):
    selected=Signal(object)

    def __init__(self,image):
        super().__init__();self.pixmap=QPixmap.fromImage(image);self.start=None;self.end=None
        self.setMinimumSize(640,360)
        self.setMouseTracking(True)

    def display_rect(self):
        size=self.pixmap.size();size.scale(self.size(),Qt.KeepAspectRatio)
        return QRect((self.width()-size.width())//2,(self.height()-size.height())//2,size.width(),size.height())

    def mousePressEvent(self,e):
        self.start=e.position().toPoint();self.end=self.start;self.update()

    def mouseMoveEvent(self,e):
        if self.start is not None and e.buttons()&Qt.LeftButton:self.end=e.position().toPoint();self.update()

    def mouseReleaseEvent(self,e):
        self.end=e.position().toPoint()
        bounds=self.display_rect();r=QRect(self.start,self.end).normalized().intersected(bounds)
        sx=self.pixmap.width()/bounds.width();sy=self.pixmap.height()/bounds.height()
        result={'left':round((r.left()-bounds.left())*sx),'top':round((r.top()-bounds.top())*sy),'width':round(r.width()*sx),'height':round(r.height()*sy)}
        if result['width']>=60 and result['height']>=60:self.selected.emit(result)
        self.update()

    def paintEvent(self,e):
        p=QPainter(self);p.fillRect(self.rect(),QColor('#10161c'));p.drawPixmap(self.display_rect(),self.pixmap)
        if self.start is not None and self.end is not None:
            p.setPen(QPen(QColor('#67e6c3'),2));p.setBrush(QColor(50,220,170,30));p.drawRect(QRect(self.start,self.end).normalized())


class RegionDialog(QDialog):
    def __init__(self,frame,monitor,parent=None,*,large=False):
        super().__init__(parent);self.setWindowTitle('Выделите большую карту · только изображение карты' if large else 'Выделите мини-карту · только изображение карты, стрелка игрока по возможности в центре')
        self.resize(1120,740);self.monitor=monitor;self.region=None
        rgb=frame[:,:,::-1].copy();h,w=rgb.shape[:2]
        image=QImage(rgb.data,w,h,rgb.strides[0],QImage.Format_RGB888).copy()
        layout=QVBoxLayout(self);layout.addWidget(QLabel(('Выделите мышью большую карту в центре экрана без рамки, шкалы координат и подсказок управления. ' if large else 'Выделите мышью мини-карту в левом нижнем углу. ')+'Значения в физических пикселях экрана, масштабирование DPI учитывается.'))
        self.canvas=RegionCanvas(image);self.canvas.selected.connect(self.select);layout.addWidget(self.canvas,1)
        self.label=QLabel('Область не выбрана');layout.addWidget(self.label)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel);self.ok=buttons.button(QDialogButtonBox.Ok);self.ok.setEnabled(False)
        buttons.accepted.connect(self.accept);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def select(self,r):
        self.region=dict(r,left=r['left']+self.monitor['left'],top=r['top']+self.monitor['top'])
        self.label.setText(f"Позиция на экране {self.region['left']}, {self.region['top']}  ·  {r['width']} × {r['height']}")
        self.ok.setEnabled(True)
