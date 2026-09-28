# SurakshAI — Anomaly Detection Module

Anomaly Detection module developed as part of **SurakshAI**, a welfare-focused backend system designed to identify unusual or suspicious patterns in welfare-related data.

This module focuses on processing welfare data, validating records, and detecting anomalous cases that may require further investigation.

## Overview

The Anomaly Detection module analyzes welfare-related records and identifies patterns that deviate significantly from expected behavior.

The system follows a modular architecture so that the anomaly detection logic can be integrated with the larger SurakshAI backend.

## Features

* Welfare data processing and validation
* Anomaly detection for identifying unusual records
* Structured data validation using Pydantic schemas
* Modular anomaly detection implementation
* Test cases for validating the detection logic
* FastAPI backend integration
* Support for processing structured welfare data
* Separation of data, schemas, detection logic, and tests

## Project Structure

```text
welfare_ai_pipeline/
│
├── anomaly/
│   └── # Anomaly detection logic
│
├── data/
│   └── # Input/sample welfare data
│
├── schemas/
│   └── # Pydantic data schemas
│
├── tests/
│   └── # Test cases
│
├── main.py
│   └── # Application entry point
│
├── requirements.txt
│   └── # Python dependencies
│
└── README.md
    └── # Project documentation
```

## Anomaly Detection Workflow

```text
Welfare Data
     │
     ▼
Data Validation
     │
     ▼
Data Preprocessing
     │
     ▼
Feature Processing
     │
     ▼
Anomaly Detection
     │
     ▼
Anomaly Score / Prediction
     │
     ▼
Flag Anomalous Records
     │
     ▼
Further Investigation
```

## Tech Stack

* **Python** — Core development
* **FastAPI** — Backend/API framework
* **Pydantic** — Data validation and schemas
* **Pandas** — Data processing
* **NumPy** — Numerical operations
* **Scikit-learn** — Machine learning and anomaly detection
* **Pytest** — Testing

## Installation

Clone the SurakshAI repository:

```bash
git clone https://github.com/prajvalsharma18/SurakshAI-backend.git
```

Navigate to the project:

```bash
cd SurakshAI-backend
```

Switch to the anomaly detection branch:

```bash
git checkout anamoly-detection
```

Navigate to the anomaly detection module:

```bash
cd welfare_ai_pipeline
```

## Create Virtual Environment

Create a Python virtual environment:

```bash
python -m venv venv
```

### Windows PowerShell

```powershell
venv\Scripts\Activate.ps1
```

### Windows CMD

```cmd
venv\Scripts\activate
```

## Install Dependencies

Install the required Python packages:

```bash
pip install -r requirements.txt
```

## Running the Application

Run the application using:

```bash
python main.py
```

If the application exposes a FastAPI app through `main.py`, it can be started using:

```bash
uvicorn main:app --reload
```

The API will be available at:

```text
http://127.0.0.1:8000
```

FastAPI interactive documentation:

```text
http://127.0.0.1:8000/docs
```

## Testing

Run the test suite using:

```bash
pytest
```

For more detailed test output:

```bash
pytest -v
```

## Development Workflow

Create a new branch before making changes:

```bash
git checkout -b feature-name
```

Add your changes:

```bash
git add .
```

Commit your changes:

```bash
git commit -m "Describe your changes"
```

Push the branch:

```bash
git push origin feature-name
```

## Important

The Python virtual environment should **not** be committed to GitHub.

Make sure `venv/` is included in `.gitignore`:

```gitignore
venv/
__pycache__/
*.pyc
.env
```

## Contributor

### Vivansh Aggarwal

**Contribution:** Anomaly Detection Module

Responsibilities include:

* Development of the anomaly detection pipeline
* Welfare data processing and validation
* Implementation of anomaly detection logic
* Creation of relevant data schemas
* Development and testing of anomaly detection components
* Integration of the module with the SurakshAI backend

## Project

**SurakshAI**

SurakshAI is a welfare-focused backend system aimed at using technology and intelligent data processing to identify potentially suspicious or anomalous welfare-related cases.

---

**Developed as part of the SurakshAI backend project.**
