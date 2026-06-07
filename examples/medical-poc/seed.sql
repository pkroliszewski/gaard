INSERT INTO patients (first_name, last_name, status, birth_date, created_at) VALUES
('Anna', 'Kowalska', 'active', '1984-02-11', '2025-01-01'),
('Jan', 'Nowak', 'active', '1978-06-24', '2025-01-02'),
('Maria', 'Wiśniewska', 'inactive', '1991-09-03', '2025-01-03'),
('Piotr', 'Zieliński', 'active', '1969-11-17', '2025-01-04'),
('Ewa', 'Wójcik', 'active', '2001-03-29', '2025-01-05');

INSERT INTO doctors (first_name, last_name, specialization) VALUES
('Tomasz', 'Kamiński', 'cardiology'),
('Alicja', 'Lewandowska', 'neurology'),
('Michał', 'Dąbrowski', 'orthopedics');

INSERT INTO appointments (patient_id, doctor_id, appointment_date, status) VALUES
(1, 1, '2026-05-01', 'completed'),
(2, 1, '2026-05-02', 'scheduled'),
(3, 2, '2026-05-03', 'cancelled'),
(4, 3, '2026-05-04', 'completed'),
(5, 2, '2026-05-05', 'scheduled');