PRAGMA foreign_keys = OFF;

DROP TABLE IF EXISTS payments;
DROP TABLE IF EXISTS lab_results;
DROP TABLE IF EXISTS prescriptions;
DROP TABLE IF EXISTS appointment_procedures;
DROP TABLE IF EXISTS appointment_diagnoses;
DROP TABLE IF EXISTS medical_procedures;
DROP TABLE IF EXISTS diagnoses;
DROP TABLE IF EXISTS appointments;
DROP TABLE IF EXISTS doctors;
DROP TABLE IF EXISTS patients;

CREATE TABLE patients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    status TEXT NOT NULL,
    sex TEXT NOT NULL,
    birth_date TEXT,
    city TEXT NOT NULL,
    phone TEXT,
    email TEXT,
    insurance_provider TEXT NOT NULL,
    risk_group TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE doctors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    first_name TEXT NOT NULL,
    last_name TEXT NOT NULL,
    specialization TEXT NOT NULL,
    license_number TEXT NOT NULL UNIQUE,
    clinic_location TEXT NOT NULL,
    room TEXT NOT NULL,
    hire_date TEXT NOT NULL,
    active INTEGER NOT NULL
);

CREATE TABLE appointments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    patient_id INTEGER NOT NULL,
    doctor_id INTEGER NOT NULL,
    appointment_date TEXT NOT NULL,
    appointment_time TEXT NOT NULL,
    duration_minutes INTEGER NOT NULL,
    status TEXT NOT NULL,
    visit_type TEXT NOT NULL,
    priority TEXT NOT NULL,
    reason TEXT NOT NULL,
    check_in_at TEXT,
    notes TEXT,
    FOREIGN KEY (patient_id) REFERENCES patients(id),
    FOREIGN KEY (doctor_id) REFERENCES doctors(id)
);

CREATE TABLE diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    icd10_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    chronic INTEGER NOT NULL
);

CREATE TABLE appointment_diagnoses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    diagnosis_id INTEGER NOT NULL,
    is_primary INTEGER NOT NULL,
    notes TEXT,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id),
    FOREIGN KEY (diagnosis_id) REFERENCES diagnoses(id)
);

CREATE TABLE medical_procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    procedure_code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    specialization TEXT NOT NULL,
    base_price REAL NOT NULL
);

CREATE TABLE appointment_procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    procedure_id INTEGER NOT NULL,
    quantity INTEGER NOT NULL,
    price REAL NOT NULL,
    result_summary TEXT,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id),
    FOREIGN KEY (procedure_id) REFERENCES medical_procedures(id)
);

CREATE TABLE prescriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    medication_name TEXT NOT NULL,
    dosage TEXT NOT NULL,
    frequency TEXT NOT NULL,
    days_supply INTEGER NOT NULL,
    issued_at TEXT NOT NULL,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id)
);

CREATE TABLE lab_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    test_name TEXT NOT NULL,
    result_value REAL NOT NULL,
    unit TEXT NOT NULL,
    reference_range TEXT NOT NULL,
    abnormal_flag TEXT NOT NULL,
    resulted_at TEXT NOT NULL,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id)
);

CREATE TABLE payments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    appointment_id INTEGER NOT NULL,
    payer_type TEXT NOT NULL,
    amount REAL NOT NULL,
    currency TEXT NOT NULL,
    status TEXT NOT NULL,
    payment_method TEXT,
    paid_at TEXT,
    FOREIGN KEY (appointment_id) REFERENCES appointments(id)
);

CREATE INDEX idx_patients_status ON patients(status);
CREATE INDEX idx_patients_city ON patients(city);
CREATE INDEX idx_doctors_specialization ON doctors(specialization);
CREATE INDEX idx_appointments_date ON appointments(appointment_date);
CREATE INDEX idx_appointments_patient ON appointments(patient_id);
CREATE INDEX idx_appointments_doctor ON appointments(doctor_id);
CREATE INDEX idx_appointment_diagnoses_appointment ON appointment_diagnoses(appointment_id);
CREATE INDEX idx_appointment_procedures_appointment ON appointment_procedures(appointment_id);
CREATE INDEX idx_payments_status ON payments(status);

PRAGMA foreign_keys = ON;
