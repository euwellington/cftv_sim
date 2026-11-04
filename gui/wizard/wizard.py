"""ProfileWizard: QWizard estilizado com os 5 passos de configuracao inicial."""
from __future__ import annotations

from typing import Any, Optional

from PyQt6.QtWidgets import QWizard

from core.models import Profile
from gui.wizard.pages_access import AccessPage
from gui.wizard.pages_brand import BrandPage
from gui.wizard.pages_mode import ModePage
from gui.wizard.pages_network import NetworkPage
from gui.wizard.pages_summary import SummaryPage


class ProfileWizard(QWizard):
    """Fabricante -> Modo -> Rede e seguranca -> Acesso -> Resumo."""

    def __init__(self) -> None:
        super().__init__()
        self.data: dict[str, Any] = {}
        self.setWindowTitle("CFTV Simulator - Configuracao inicial")
        self.setWizardStyle(QWizard.WizardStyle.ClassicStyle)
        self.setOption(QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.setOption(QWizard.WizardOption.HaveHelpButton, False)
        self.setButtonText(QWizard.WizardButton.NextButton, "Avancar >")
        self.setButtonText(QWizard.WizardButton.BackButton, "< Voltar")
        self.setButtonText(QWizard.WizardButton.CancelButton, "Cancelar")
        self.setButtonText(QWizard.WizardButton.FinishButton, "Iniciar")
        self.resize(900, 680)
        for page in (BrandPage(), ModePage(), NetworkPage(), AccessPage(), SummaryPage()):
            self.addPage(page)

    def result_profile(self) -> Optional[Profile]:
        """Perfil pronto (apos ``exec()`` aceito)."""
        return self.data.get("final_profile")
