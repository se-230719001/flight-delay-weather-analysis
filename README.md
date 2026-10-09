# flight-delay-weather-analysis

## The Question

This integrated dataset will allow us to predict and explain flight route anomalies and ground arrival delays caused by localized severe weather events. By combining real-time flight vectors with atmospheric metrics (such as wind gusts and convective precipitation), we can model how adverse conditions alter standard descent profiles and cruise velocities. Ultimately, this analysis will help determine the specific weather thresholds that disproportionately increase airspace congestion around major flight corridors.

## Data Sources

Detailed documentation, licensing terms, and metadata schemas for each source are in the source cards: [docs/sources.md](docs/sources.md).

- **OpenSky Network (ADS-B flight telemetry):** live and historical state vectors of global flights. See [docs/sources.md#opensky-network](docs/sources.md#opensky-network).
- **Open-Meteo API (atmospheric and weather metrics):** real-time and forecasted meteorological data. See [docs/sources.md#open-meteo](docs/sources.md#open-meteo).

### Attribution

- Flight data provided by [The OpenSky Network](https://opensky-network.org).
- Weather data provided by [Open-Meteo.com](https://open-meteo.com) under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/).

## How to Run

Follow the steps below to set up the environment and run the ingestion pipeline from a clean clone.

### Prerequisites

- Python 3.10+
- Git

### Setup and Execution

1. **Clone the repository**

   ```bash
   git clone https://github.com/se-230717022/flight-delay-weather-analysis.git
   cd flight-delay-weather-analysis
   ```

2. **Create and activate a virtual environment** (recommended)

   ```bash
   python -m venv venv

   # macOS / Linux
   source venv/bin/activate

   # Windows
   venv\Scripts\activate
   ```

3. **Install dependencies**

   ```bash
   pip install -r requirements.txt
   ```

4. **Configure environment variables**

   Copy the example file and fill in your credentials:

   ```bash
   cp .env.example .env
   ```

   Open `.env` and set:

   ```
   OPENSKY_USERNAME=your_username_here
   OPENSKY_PASSWORD=your_password_here
   ```

   Note: OpenSky allows limited anonymous requests, but using valid credentials avoids strict anonymous rate limits. Open-Meteo requires no API key. Never commit `.env`; it is listed in `.gitignore`.

5. **Run the ingestion script** (single command, from the project root)

   ```bash
   python src/ingest.py
   ```

6. **Verify the output**

   Raw, unmodified JSON snapshots with UTC retrieval timestamps are written to `data/raw/`. Earlier files are never overwritten; every run creates new files:

   ```
   data/raw/opensky/YYYYMMDDTHHMMSSZ.json
   data/raw/openmeteo/YYYYMMDDTHHMMSSZ.json
   ```

   A run summary (sources fetched, number of records or files per source, and any failures) is printed to the terminal.

## Repository Structure

```
flight-delay-weather-analysis/
├── README.md            # Project overview, setup, and execution guide
├── .gitignore           # Git ignore file (ensures secrets and local caches are untracked)
├── .env.example         # Template for required environment variables
├── requirements.txt     # Frozen list of project Python dependencies
├── data/
│   └── raw/             # Landing zone for raw, untouched JSON API responses
│       ├── opensky/     # Snapshots from OpenSky Network
│       └── openmeteo/   # Snapshots from Open-Meteo API
├── src/
│   └── ingest.py        # Standalone ingestion script handling extraction, retries, and logging
└── docs/
    └── sources.md       # Data source cards detailing schema, licensing, and known issues
```

## Status

M1 — ingestion complete

## Team

| Name | GitHub |
|------|--------|
| Enes Akdeniz (230717022) | [@se-230717022](https://github.com/se-230717022) |
| Nihat Avcı (230719001) | [@se-230719001](https://github.com/se-230719001) |
| Mehmetali Açık (240719001) | [@se-240719001](https://github.com/se-240719001) |
