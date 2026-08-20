# extractors/base_extractor.py
# Ported from app/BackEnd/Metadata_Scanner/extractors/base_extractor.py
# No changes needed — pure Python, no FastAPI dependencies.

from abc import ABC, abstractmethod


class BaseExtractor(ABC):

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def extract(self):
        pass

    @abstractmethod
    def close(self):
        pass
