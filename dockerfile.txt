# 1. Wybierz odpowiedni obraz bazowy RunPod z PyTorch i CUDA
# Sprawdź najnowsze/odpowiednie tagi na: https://hub.docker.com/r/runpod/pytorch/tags
# Wybieram wersję z PyTorch 2.1, Python 3.10, CUDA 12.1.1 na Ubuntu 22.04
# Jest to popularny i dość aktualny wybór. Dostosuj, jeśli potrzebujesz innej wersji.
FROM runpod/pytorch:2.1.0-py3.10-cuda12.1.1-devel-ubuntu22.04

# 2. Ustaw katalog roboczy wewnątrz kontenera
WORKDIR /workspace

# 3. Zainstaluj niezbędne zależności systemowe
#    - git: kluczowy, aby RunPod mógł sklonować Twoje repozytorium LUB abyś Ty mógł używać gita wewnątrz kontenera.
#    - ffmpeg: często wymagany przy przetwarzaniu wideo/ramek.
#    Dodaj inne pakiety, jeśli okażą się potrzebne (np. libgl1-mesa-glx jeśli używasz OpenCV z GUI)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    ffmpeg \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# 4. Skopiuj tylko plik z zależnościami Pythona
#    Robimy to przed skopiowaniem reszty kodu (którego tu nie robimy),
#    aby wykorzystać cache warstw Dockera.
COPY requirements.txt ./

# 5. Zainstaluj zależności Pythona
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# 6. Kopiowanie kodu aplikacji (POMIJANE w tym scenariuszu)
#    Zakładamy, że RunPod sklonuje repozytorium GitHub do /workspace
#    podczas uruchamiania Poda. Nie "wypalamy" kodu w obrazie.
# COPY . .

# 7. Ustaw domyślne polecenie
#    Uruchomienie powłoki bash jest dobrym punktem wyjścia.
#    Polecenie startowe dla Poda zdefiniujesz w interfejsie RunPod.
CMD ["/bin/bash"]

# Opcjonalnie: Jeśli FramePack udostępnia jakiś interfejs webowy/API,
# odkomentuj i ustaw odpowiedni port. Domyślnie nie jest to potrzebne.
# EXPOSE 8080