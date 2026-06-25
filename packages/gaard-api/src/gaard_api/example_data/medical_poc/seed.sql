DELETE FROM payments;
DELETE FROM lab_results;
DELETE FROM prescriptions;
DELETE FROM appointment_procedures;
DELETE FROM appointment_diagnoses;
DELETE FROM medical_procedures;
DELETE FROM diagnoses;
DELETE FROM appointments;
DELETE FROM patients;
DELETE FROM doctors;
DELETE FROM sqlite_sequence WHERE name IN (
    'payments',
    'lab_results',
    'prescriptions',
    'appointment_procedures',
    'appointment_diagnoses',
    'medical_procedures',
    'diagnoses',
    'appointments',
    'patients',
    'doctors'
);

WITH RECURSIVE
    seq(n) AS (
        VALUES (1)
        UNION ALL
        SELECT n + 1 FROM seq WHERE n < 140
    ),
    first_names(id, first_name) AS (
        VALUES
            (1, 'Anna'), (2, 'Jan'), (3, 'Maria'), (4, 'Piotr'), (5, 'Ewa'),
            (6, 'Tomasz'), (7, 'Katarzyna'), (8, 'Michał'), (9, 'Agnieszka'), (10, 'Paweł'),
            (11, 'Magdalena'), (12, 'Łukasz'), (13, 'Joanna'), (14, 'Krzysztof'), (15, 'Monika'),
            (16, 'Marcin'), (17, 'Aleksandra'), (18, 'Grzegorz'), (19, 'Natalia'), (20, 'Adam'),
            (21, 'Karolina'), (22, 'Mateusz'), (23, 'Dorota'), (24, 'Rafał'), (25, 'Beata'),
            (26, 'Bartosz'), (27, 'Iwona'), (28, 'Robert'), (29, 'Zofia'), (30, 'Wojciech')
    ),
    last_names(id, last_name) AS (
        VALUES
            (1, 'Kowalska'), (2, 'Nowak'), (3, 'Wiśniewska'), (4, 'Wójcik'), (5, 'Kowalczyk'),
            (6, 'Kamiński'), (7, 'Lewandowska'), (8, 'Zieliński'), (9, 'Szymańska'), (10, 'Woźniak'),
            (11, 'Dąbrowska'), (12, 'Kozłowski'), (13, 'Jankowska'), (14, 'Mazur'), (15, 'Kwiatkowska'),
            (16, 'Wojciechowski'), (17, 'Krawczyk'), (18, 'Kaczmarek'), (19, 'Piotrowska'), (20, 'Grabowski'),
            (21, 'Nowakowska'), (22, 'Pawłowski'), (23, 'Michalska'), (24, 'Król'), (25, 'Wieczorek'),
            (26, 'Jabłońska'), (27, 'Wróbel'), (28, 'Majewska'), (29, 'Olszewski'), (30, 'Jaworska'),
            (31, 'Malinowski'), (32, 'Stępień'), (33, 'Dudek'), (34, 'Adamczyk'), (35, 'Pawlak'),
            (36, 'Górska'), (37, 'Sikora'), (38, 'Baran'), (39, 'Rutkowska'), (40, 'Szewczyk'),
            (41, 'Ostrowska'), (42, 'Tomaszewski'), (43, 'Pietrzak'), (44, 'Zalewska'), (45, 'Wróblewski'),
            (46, 'Marciniak'), (47, 'Jasińska'), (48, 'Sadowski'), (49, 'Bąk'), (50, 'Chmielewska')
    ),
    cities(id, city) AS (
        VALUES
            (1, 'Warszawa'), (2, 'Kraków'), (3, 'Łódź'), (4, 'Wrocław'),
            (5, 'Poznań'), (6, 'Gdańsk'), (7, 'Katowice'), (8, 'Lublin'),
            (9, 'Szczecin'), (10, 'Bydgoszcz'), (11, 'Białystok'), (12, 'Rzeszów')
    ),
    insurers(id, insurance_provider) AS (
        VALUES
            (1, 'NFZ'), (2, 'Lux Med'), (3, 'Medicover'), (4, 'PZU Zdrowie'), (5, 'Enel-Med')
    ),
    current_bounds(year_start, today, elapsed_days) AS (
        SELECT
            date('now', 'localtime', 'start of year'),
            date('now', 'localtime'),
            CAST(julianday(date('now', 'localtime')) - julianday(date('now', 'localtime', 'start of year')) AS INTEGER)
    )
INSERT INTO patients (
    id,
    first_name,
    last_name,
    status,
    sex,
    birth_date,
    city,
    phone,
    email,
    insurance_provider,
    risk_group,
    created_at
)
SELECT
    seq.n,
    first_names.first_name,
    last_names.last_name,
    CASE
        WHEN seq.n % 19 = 0 OR seq.n % 37 = 0 THEN 'inactive'
        ELSE 'active'
    END,
    CASE WHEN seq.n % 2 = 0 THEN 'M' ELSE 'F' END,
    printf(
        '%04d-%02d-%02d',
        1940 + ((seq.n * 37) % 72),
        1 + ((seq.n * 5) % 12),
        1 + ((seq.n * 11) % 28)
    ),
    cities.city,
    printf('+48 5%02d %03d %03d', (seq.n * 7) % 100, (seq.n * 83) % 1000, (seq.n * 197) % 1000),
    'patient' || seq.n || '@example.med',
    insurers.insurance_provider,
    CASE
        WHEN seq.n % 11 = 0 OR seq.n % 17 = 0 THEN 'high'
        WHEN seq.n % 5 = 0 OR seq.n % 7 = 0 THEN 'medium'
        ELSE 'low'
    END,
    CASE
        WHEN seq.n = 1 THEN current_bounds.year_start
        WHEN seq.n = 140 THEN current_bounds.today
        ELSE date(current_bounds.year_start, '+' || ((seq.n * 7) % (current_bounds.elapsed_days + 1)) || ' days')
    END
FROM seq
JOIN first_names ON first_names.id = 1 + ((seq.n - 1) % 30)
JOIN last_names ON last_names.id = 1 + (((seq.n - 1) * 17) % 50)
JOIN cities ON cities.id = 1 + (((seq.n - 1) * 5) % 12)
JOIN insurers ON insurers.id = 1 + (((seq.n - 1) * 3) % 5)
CROSS JOIN current_bounds;

INSERT INTO doctors (
    id,
    first_name,
    last_name,
    specialization,
    license_number,
    clinic_location,
    room,
    hire_date,
    active
) VALUES
(1, 'Tomasz', 'Kamiński', 'cardiology', 'PWZ-100001', 'Warszawa - Mokotów', 'A101', '2012-04-01', 1),
(2, 'Alicja', 'Lewandowska', 'cardiology', 'PWZ-100002', 'Warszawa - Mokotów', 'A102', '2017-09-15', 1),
(3, 'Marek', 'Kubiak', 'cardiology', 'PWZ-100003', 'Kraków - Podgórze', 'C201', '2014-02-10', 1),
(4, 'Dorota', 'Szulc', 'cardiology', 'PWZ-100004', 'Wrocław - Centrum', 'B112', '2019-06-01', 1),
(5, 'Paweł', 'Czarnecki', 'cardiology', 'PWZ-100005', 'Poznań - Jeżyce', 'D014', '2015-11-20', 1),
(6, 'Joanna', 'Gajda', 'cardiology', 'PWZ-100006', 'Gdańsk - Wrzeszcz', 'G203', '2021-01-05', 1),
(7, 'Michał', 'Dąbrowski', 'neurology', 'PWZ-100007', 'Warszawa - Mokotów', 'N101', '2011-03-14', 1),
(8, 'Katarzyna', 'Witkowska', 'neurology', 'PWZ-100008', 'Łódź - Śródmieście', 'N207', '2018-08-27', 1),
(9, 'Łukasz', 'Nowicki', 'neurology', 'PWZ-100009', 'Kraków - Podgórze', 'N011', '2016-05-23', 1),
(10, 'Ewa', 'Lis', 'neurology', 'PWZ-100010', 'Wrocław - Centrum', 'N105', '2020-10-12', 1),
(11, 'Robert', 'Sawicki', 'neurology', 'PWZ-100011', 'Poznań - Jeżyce', 'N305', '2013-12-02', 1),
(12, 'Magdalena', 'Cieślak', 'neurology', 'PWZ-100012', 'Gdańsk - Wrzeszcz', 'N208', '2022-04-18', 1),
(13, 'Grzegorz', 'Maj', 'orthopedics', 'PWZ-100013', 'Warszawa - Mokotów', 'O101', '2010-01-11', 1),
(14, 'Anna', 'Sobczak', 'orthopedics', 'PWZ-100014', 'Łódź - Śródmieście', 'O208', '2016-07-01', 1),
(15, 'Rafał', 'Wrona', 'orthopedics', 'PWZ-100015', 'Kraków - Podgórze', 'O022', '2019-03-06', 1),
(16, 'Monika', 'Madej', 'orthopedics', 'PWZ-100016', 'Wrocław - Centrum', 'O114', '2015-05-19', 1),
(17, 'Bartosz', 'Kopeć', 'orthopedics', 'PWZ-100017', 'Poznań - Jeżyce', 'O309', '2020-02-24', 1),
(18, 'Iwona', 'Borkowska', 'orthopedics', 'PWZ-100018', 'Gdańsk - Wrzeszcz', 'O204', '2018-11-08', 1),
(19, 'Karolina', 'Urbańska', 'dermatology', 'PWZ-100019', 'Warszawa - Mokotów', 'D101', '2012-09-03', 1),
(20, 'Wojciech', 'Czerwiński', 'dermatology', 'PWZ-100020', 'Łódź - Śródmieście', 'D206', '2017-04-13', 1),
(21, 'Beata', 'Kalinowska', 'dermatology', 'PWZ-100021', 'Kraków - Podgórze', 'D014', '2021-06-30', 1),
(22, 'Adam', 'Sokołowski', 'dermatology', 'PWZ-100022', 'Wrocław - Centrum', 'D109', '2014-12-15', 1),
(23, 'Natalia', 'Walczak', 'dermatology', 'PWZ-100023', 'Poznań - Jeżyce', 'D310', '2019-09-09', 1),
(24, 'Krzysztof', 'Rutkowski', 'dermatology', 'PWZ-100024', 'Gdańsk - Wrzeszcz', 'D202', '2022-01-17', 1);

INSERT INTO diagnoses (id, icd10_code, name, category, chronic) VALUES
(1, 'I10', 'Essential hypertension', 'cardiology', 1),
(2, 'I25', 'Chronic ischemic heart disease', 'cardiology', 1),
(3, 'I48', 'Atrial fibrillation and flutter', 'cardiology', 1),
(4, 'I50', 'Heart failure', 'cardiology', 1),
(5, 'R07', 'Chest pain', 'cardiology', 0),
(6, 'G43', 'Migraine', 'neurology', 1),
(7, 'G40', 'Epilepsy', 'neurology', 1),
(8, 'G56', 'Mononeuropathies of upper limb', 'neurology', 1),
(9, 'G47', 'Sleep disorders', 'neurology', 1),
(10, 'R51', 'Headache', 'neurology', 0),
(11, 'M54', 'Dorsalgia', 'orthopedics', 0),
(12, 'M17', 'Gonarthrosis', 'orthopedics', 1),
(13, 'M25', 'Other joint disorders', 'orthopedics', 0),
(14, 'S93', 'Dislocation and sprain of ankle', 'orthopedics', 0),
(15, 'M75', 'Shoulder lesions', 'orthopedics', 1),
(16, 'L20', 'Atopic dermatitis', 'dermatology', 1),
(17, 'L40', 'Psoriasis', 'dermatology', 1),
(18, 'L70', 'Acne', 'dermatology', 0),
(19, 'L50', 'Urticaria', 'dermatology', 0),
(20, 'R21', 'Rash and other skin eruption', 'dermatology', 0),
(21, 'E11', 'Type 2 diabetes mellitus', 'metabolic', 1),
(22, 'E78', 'Disorders of lipoprotein metabolism', 'metabolic', 1),
(23, 'J06', 'Acute upper respiratory infections', 'primary care', 0),
(24, 'F41', 'Other anxiety disorders', 'mental health', 1),
(25, 'Z00', 'General medical examination', 'preventive', 0),
(26, 'Z09', 'Follow-up examination after treatment', 'follow up', 0),
(27, 'Z71', 'Persons encountering health services for counselling', 'counselling', 0),
(28, 'R53', 'Malaise and fatigue', 'general symptoms', 0);

INSERT INTO medical_procedures (id, procedure_code, name, specialization, base_price) VALUES
(1, 'CARD-ECG', 'Resting ECG', 'cardiology', 95.00),
(2, 'CARD-ECHO', 'Echocardiography', 'cardiology', 260.00),
(3, 'CARD-HOLTER', '24h ECG Holter', 'cardiology', 180.00),
(4, 'CARD-ABPM', '24h blood pressure monitoring', 'cardiology', 150.00),
(5, 'CARD-CONS', 'Cardiology consultation', 'cardiology', 220.00),
(6, 'NEUR-CONS', 'Neurology consultation', 'neurology', 220.00),
(7, 'NEUR-EEG', 'EEG examination', 'neurology', 250.00),
(8, 'NEUR-EMG', 'EMG examination', 'neurology', 320.00),
(9, 'NEUR-HEAD', 'Headache assessment', 'neurology', 190.00),
(10, 'NEUR-BAL', 'Balance assessment', 'neurology', 160.00),
(11, 'ORTH-CONS', 'Orthopedic consultation', 'orthopedics', 210.00),
(12, 'ORTH-USG', 'Joint ultrasound', 'orthopedics', 230.00),
(13, 'ORTH-XRAY', 'X-ray referral review', 'orthopedics', 80.00),
(14, 'ORTH-INJ', 'Intra-articular injection', 'orthopedics', 180.00),
(15, 'ORTH-PHYS', 'Physiotherapy qualification', 'orthopedics', 120.00),
(16, 'DERM-CONS', 'Dermatology consultation', 'dermatology', 190.00),
(17, 'DERM-SCOPE', 'Dermatoscopy', 'dermatology', 140.00),
(18, 'DERM-BIOP', 'Skin biopsy', 'dermatology', 260.00),
(19, 'DERM-CRYO', 'Cryotherapy', 'dermatology', 120.00),
(20, 'DERM-ALL', 'Skin allergy test', 'dermatology', 170.00),
(21, 'GEN-TELE', 'Medical teleconsultation', 'general', 120.00);

WITH RECURSIVE
    seq(n) AS (
        VALUES (1)
        UNION ALL
        SELECT n + 1 FROM seq WHERE n < 420
    ),
    current_bounds(year_start, today, elapsed_days) AS (
        SELECT
            date('now', 'localtime', 'start of year'),
            date('now', 'localtime'),
            CAST(julianday(date('now', 'localtime')) - julianday(date('now', 'localtime', 'start of year')) AS INTEGER)
    ),
    appointment_offsets(n, patient_id, doctor_id, day_offset) AS (
        SELECT
            seq.n,
            CASE
                WHEN seq.n <= 140 THEN seq.n
                WHEN seq.n % 18 = 0 THEN 7
                WHEN seq.n % 22 = 0 THEN 31
                WHEN seq.n % 27 = 0 THEN 86
                ELSE 1 + ((seq.n * 29) % 140)
            END,
            CASE
                WHEN seq.n <= 24 THEN seq.n
                ELSE 1 + ((seq.n * 7) % 24)
            END,
            CASE
                WHEN seq.n = 420 THEN current_bounds.elapsed_days
                WHEN seq.n <= 112 THEN CAST((seq.n - 1) / 8 AS INTEGER) % (current_bounds.elapsed_days + 1)
                ELSE ((seq.n * 37 + CAST(seq.n / 9 AS INTEGER)) % (current_bounds.elapsed_days + 1))
            END
        FROM seq
        CROSS JOIN current_bounds
    ),
    appointment_data AS (
        SELECT
            appointment_offsets.n,
            appointment_offsets.patient_id,
            appointment_offsets.doctor_id,
            date(current_bounds.year_start, '+' || appointment_offsets.day_offset || ' days') AS appointment_date,
            printf('%02d:%02d', 8 + ((appointment_offsets.n * 7) % 10), (appointment_offsets.n * 15) % 60) AS appointment_time,
            CASE
                WHEN appointment_offsets.n % 10 = 0 THEN 60
                WHEN appointment_offsets.n % 4 = 0 THEN 45
                WHEN appointment_offsets.n % 3 = 0 THEN 15
                ELSE 30
            END AS duration_minutes,
            CASE
                WHEN appointment_offsets.day_offset = current_bounds.elapsed_days THEN
                    CASE
                        WHEN appointment_offsets.n % 5 = 0 THEN 'cancelled'
                        WHEN appointment_offsets.n % 3 = 0 THEN 'checked_in'
                        ELSE 'scheduled'
                    END
                WHEN appointment_offsets.n % 23 = 0 THEN 'no_show'
                WHEN appointment_offsets.n % 17 = 0 THEN 'cancelled'
                ELSE 'completed'
            END AS status,
            CASE appointment_offsets.n % 6
                WHEN 0 THEN 'telemedicine'
                WHEN 1 THEN 'first_visit'
                WHEN 2 THEN 'follow_up'
                WHEN 3 THEN 'control'
                WHEN 4 THEN 'urgent'
                ELSE 'preventive'
            END AS visit_type,
            CASE
                WHEN appointment_offsets.n % 29 = 0 THEN 'high'
                WHEN appointment_offsets.n % 11 = 0 THEN 'elevated'
                ELSE 'routine'
            END AS priority,
            CASE
                WHEN appointment_offsets.doctor_id BETWEEN 1 AND 6 THEN
                    CASE appointment_offsets.n % 5
                        WHEN 0 THEN 'blood pressure follow-up'
                        WHEN 1 THEN 'chest pain evaluation'
                        WHEN 2 THEN 'palpitations'
                        WHEN 3 THEN 'shortness of breath'
                        ELSE 'post-treatment control'
                    END
                WHEN appointment_offsets.doctor_id BETWEEN 7 AND 12 THEN
                    CASE appointment_offsets.n % 5
                        WHEN 0 THEN 'migraine follow-up'
                        WHEN 1 THEN 'dizziness'
                        WHEN 2 THEN 'sleep disorder'
                        WHEN 3 THEN 'limb numbness'
                        ELSE 'seizure control'
                    END
                WHEN appointment_offsets.doctor_id BETWEEN 13 AND 18 THEN
                    CASE appointment_offsets.n % 5
                        WHEN 0 THEN 'knee pain'
                        WHEN 1 THEN 'back pain'
                        WHEN 2 THEN 'ankle injury'
                        WHEN 3 THEN 'shoulder mobility issue'
                        ELSE 'post-injury control'
                    END
                ELSE
                    CASE appointment_offsets.n % 5
                        WHEN 0 THEN 'rash assessment'
                        WHEN 1 THEN 'mole check'
                        WHEN 2 THEN 'acne treatment'
                        WHEN 3 THEN 'psoriasis follow-up'
                        ELSE 'allergy symptoms'
                    END
            END AS reason
        FROM appointment_offsets
        CROSS JOIN current_bounds
    )
INSERT INTO appointments (
    id,
    patient_id,
    doctor_id,
    appointment_date,
    appointment_time,
    duration_minutes,
    status,
    visit_type,
    priority,
    reason,
    check_in_at,
    notes
)
SELECT
    appointment_data.n,
    appointment_data.patient_id,
    appointment_data.doctor_id,
    appointment_data.appointment_date,
    appointment_data.appointment_time,
    appointment_data.duration_minutes,
    appointment_data.status,
    appointment_data.visit_type,
    appointment_data.priority,
    appointment_data.reason,
    CASE
        WHEN appointment_data.status IN ('completed', 'checked_in') THEN appointment_data.appointment_date || ' ' || appointment_data.appointment_time
        ELSE NULL
    END,
    CASE
        WHEN appointment_data.status = 'cancelled' THEN 'Cancelled by patient or clinic before check-in.'
        WHEN appointment_data.status = 'no_show' THEN 'Patient did not arrive for the planned visit.'
        WHEN appointment_data.priority = 'high' THEN 'High-priority clinical review requested.'
        ELSE NULL
    END
FROM appointment_data;

WITH primary_diagnoses AS (
    SELECT
        appointments.id AS appointment_id,
        CASE doctors.specialization
            WHEN 'cardiology' THEN 1 + ((appointments.id * 3) % 5)
            WHEN 'neurology' THEN 6 + ((appointments.id * 5) % 5)
            WHEN 'orthopedics' THEN 11 + ((appointments.id * 7) % 5)
            ELSE 16 + ((appointments.id * 11) % 5)
        END AS diagnosis_id
    FROM appointments
    JOIN doctors ON doctors.id = appointments.doctor_id
    WHERE appointments.status = 'completed'
)
INSERT INTO appointment_diagnoses (appointment_id, diagnosis_id, is_primary, notes)
SELECT
    appointment_id,
    diagnosis_id,
    1,
    'Primary diagnosis recorded after completed visit.'
FROM primary_diagnoses;

INSERT INTO appointment_diagnoses (appointment_id, diagnosis_id, is_primary, notes)
SELECT
    appointments.id,
    CASE appointments.id % 5
        WHEN 0 THEN 21
        WHEN 1 THEN 22
        WHEN 2 THEN 23
        WHEN 3 THEN 24
        ELSE 25
    END,
    0,
    'Secondary condition relevant for analysis.'
FROM appointments
WHERE appointments.status = 'completed'
  AND appointments.id % 4 = 0;

WITH base_procedures AS (
    SELECT
        appointments.id AS appointment_id,
        CASE doctors.specialization
            WHEN 'cardiology' THEN 5
            WHEN 'neurology' THEN 6
            WHEN 'orthopedics' THEN 11
            ELSE 16
        END AS procedure_id,
        CASE doctors.specialization
            WHEN 'cardiology' THEN 'Cardiology consultation completed.'
            WHEN 'neurology' THEN 'Neurology consultation completed.'
            WHEN 'orthopedics' THEN 'Orthopedic consultation completed.'
            ELSE 'Dermatology consultation completed.'
        END AS result_summary
    FROM appointments
    JOIN doctors ON doctors.id = appointments.doctor_id
    WHERE appointments.status IN ('completed', 'checked_in')
)
INSERT INTO appointment_procedures (appointment_id, procedure_id, quantity, price, result_summary)
SELECT
    base_procedures.appointment_id,
    base_procedures.procedure_id,
    1,
    medical_procedures.base_price,
    base_procedures.result_summary
FROM base_procedures
JOIN medical_procedures ON medical_procedures.id = base_procedures.procedure_id;

WITH additional_procedures AS (
    SELECT
        appointments.id AS appointment_id,
        CASE doctors.specialization
            WHEN 'cardiology' THEN 1 + (CAST(appointments.id / 3 AS INTEGER) % 4)
            WHEN 'neurology' THEN 7 + (CAST(appointments.id / 3 AS INTEGER) % 4)
            WHEN 'orthopedics' THEN 12 + (CAST(appointments.id / 3 AS INTEGER) % 4)
            ELSE 17 + (CAST(appointments.id / 3 AS INTEGER) % 4)
        END AS procedure_id
    FROM appointments
    JOIN doctors ON doctors.id = appointments.doctor_id
    WHERE appointments.status = 'completed'
      AND appointments.id % 3 = 0
)
INSERT INTO appointment_procedures (appointment_id, procedure_id, quantity, price, result_summary)
SELECT
    additional_procedures.appointment_id,
    additional_procedures.procedure_id,
    1,
    medical_procedures.base_price,
    'Additional diagnostic or therapeutic procedure.'
FROM additional_procedures
JOIN medical_procedures ON medical_procedures.id = additional_procedures.procedure_id;

INSERT INTO prescriptions (
    appointment_id,
    medication_name,
    dosage,
    frequency,
    days_supply,
    issued_at
)
SELECT
    appointments.id,
    CASE doctors.specialization
        WHEN 'cardiology' THEN
            CASE appointments.id % 4
                WHEN 0 THEN 'Amlodipine'
                WHEN 1 THEN 'Bisoprolol'
                WHEN 2 THEN 'Atorvastatin'
                ELSE 'Ramipril'
            END
        WHEN 'neurology' THEN
            CASE appointments.id % 4
                WHEN 0 THEN 'Sumatriptan'
                WHEN 1 THEN 'Pregabalin'
                WHEN 2 THEN 'Levetiracetam'
                ELSE 'Melatonin'
            END
        WHEN 'orthopedics' THEN
            CASE appointments.id % 4
                WHEN 0 THEN 'Naproxen'
                WHEN 1 THEN 'Diclofenac gel'
                WHEN 2 THEN 'Paracetamol'
                ELSE 'Vitamin D3'
            END
        ELSE
            CASE appointments.id % 4
                WHEN 0 THEN 'Cetirizine'
                WHEN 1 THEN 'Hydrocortisone cream'
                WHEN 2 THEN 'Clindamycin gel'
                ELSE 'Emollient cream'
            END
    END,
    CASE appointments.id % 3
        WHEN 0 THEN '1 tablet'
        WHEN 1 THEN '5 mg'
        ELSE 'thin layer'
    END,
    CASE appointments.id % 4
        WHEN 0 THEN 'once daily'
        WHEN 1 THEN 'twice daily'
        WHEN 2 THEN 'as needed'
        ELSE 'at bedtime'
    END,
    CASE
        WHEN appointments.id % 11 = 0 THEN 90
        WHEN appointments.id % 5 = 0 THEN 60
        ELSE 30
    END,
    appointments.appointment_date || ' ' || appointments.appointment_time
FROM appointments
JOIN doctors ON doctors.id = appointments.doctor_id
WHERE appointments.status = 'completed'
  AND appointments.id % 2 = 0;

WITH specialty_labs AS (
    SELECT
        appointments.id AS appointment_id,
        CASE doctors.specialization
            WHEN 'cardiology' THEN 'LDL cholesterol'
            WHEN 'neurology' THEN 'Vitamin B12'
            WHEN 'orthopedics' THEN 'CRP'
            ELSE 'IgE total'
        END AS test_name,
        CASE doctors.specialization
            WHEN 'cardiology' THEN 70 + ((appointments.id * 3) % 90)
            WHEN 'neurology' THEN 180 + ((appointments.id * 11) % 520)
            WHEN 'orthopedics' THEN 1 + ((appointments.id * 2) % 40)
            ELSE 20 + ((appointments.id * 9) % 430)
        END AS result_value,
        CASE doctors.specialization
            WHEN 'cardiology' THEN 'mg/dL'
            WHEN 'neurology' THEN 'pg/mL'
            WHEN 'orthopedics' THEN 'mg/L'
            ELSE 'IU/mL'
        END AS unit,
        CASE doctors.specialization
            WHEN 'cardiology' THEN '< 115'
            WHEN 'neurology' THEN '200 - 900'
            WHEN 'orthopedics' THEN '< 5'
            ELSE '< 100'
        END AS reference_range,
        CASE
            WHEN appointments.id % 7 = 0 THEN 'high'
            WHEN appointments.id % 13 = 0 THEN 'low'
            ELSE 'normal'
        END AS abnormal_flag,
        date(appointments.appointment_date, '+1 day') || ' 08:00' AS resulted_at
    FROM appointments
    JOIN doctors ON doctors.id = appointments.doctor_id
    WHERE appointments.status = 'completed'
      AND appointments.id % 3 = 0
),
general_labs AS (
    SELECT
        appointments.id AS appointment_id,
        'Complete blood count' AS test_name,
        4.0 + ((appointments.id * 17) % 70) / 10.0 AS result_value,
        '10^9/L' AS unit,
        '4.0 - 10.0' AS reference_range,
        CASE
            WHEN appointments.id % 9 = 0 THEN 'high'
            WHEN appointments.id % 14 = 0 THEN 'low'
            ELSE 'normal'
        END AS abnormal_flag,
        date(appointments.appointment_date, '+1 day') || ' 09:00' AS resulted_at
    FROM appointments
    WHERE appointments.status = 'completed'
      AND appointments.id % 5 = 0
)
INSERT INTO lab_results (
    appointment_id,
    test_name,
    result_value,
    unit,
    reference_range,
    abnormal_flag,
    resulted_at
)
SELECT appointment_id, test_name, result_value, unit, reference_range, abnormal_flag, resulted_at FROM specialty_labs
UNION ALL
SELECT appointment_id, test_name, result_value, unit, reference_range, abnormal_flag, resulted_at FROM general_labs;

WITH procedure_totals AS (
    SELECT
        appointment_id,
        SUM(price * quantity) AS procedure_amount
    FROM appointment_procedures
    GROUP BY appointment_id
),
payment_data AS (
    SELECT
        appointments.id AS appointment_id,
        CASE
            WHEN patients.insurance_provider = 'NFZ' THEN 'public'
            WHEN appointments.id % 4 = 0 THEN 'private'
            ELSE 'insurance'
        END AS payer_type,
        CASE
            WHEN appointments.status IN ('cancelled', 'scheduled') THEN 0
            WHEN appointments.status = 'no_show' THEN 80
            ELSE COALESCE(procedure_totals.procedure_amount, 0)
        END AS amount,
        CASE
            WHEN appointments.status = 'cancelled' THEN 'waived'
            WHEN appointments.status IN ('scheduled', 'checked_in') THEN 'pending'
            WHEN appointments.status = 'no_show' THEN
                CASE WHEN appointments.id % 2 = 0 THEN 'pending' ELSE 'waived' END
            WHEN appointments.id % 10 = 0 THEN 'overdue'
            WHEN appointments.id % 8 = 0 THEN 'pending'
            ELSE 'paid'
        END AS payment_status,
        appointments.appointment_date,
        appointments.appointment_time
    FROM appointments
    JOIN patients ON patients.id = appointments.patient_id
    LEFT JOIN procedure_totals ON procedure_totals.appointment_id = appointments.id
)
INSERT INTO payments (
    appointment_id,
    payer_type,
    amount,
    currency,
    status,
    payment_method,
    paid_at
)
SELECT
    payment_data.appointment_id,
    payment_data.payer_type,
    payment_data.amount,
    'PLN',
    payment_data.payment_status,
    CASE
        WHEN payment_data.payment_status = 'paid' THEN
            CASE payment_data.appointment_id % 3
                WHEN 0 THEN 'card'
                WHEN 1 THEN 'cash'
                ELSE 'bank_transfer'
            END
        ELSE NULL
    END,
    CASE
        WHEN payment_data.payment_status = 'paid' THEN payment_data.appointment_date || ' ' || payment_data.appointment_time
        ELSE NULL
    END
FROM payment_data;
