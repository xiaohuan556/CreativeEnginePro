"""CreativeEnginePro shared widgets."""

from PyQt6.QtCore import QSize
from PyQt6.QtWidgets import QCheckBox


class CheckMarkBox(QCheckBox):
    """A checkbox with a hit target covering its full widget rectangle."""

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def sizeHint(self):
        return super().sizeHint().expandedTo(QSize(0, 24))
