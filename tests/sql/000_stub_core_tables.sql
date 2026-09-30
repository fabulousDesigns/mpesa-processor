-- TEST ONLY: minimal stand-ins for the Nest-owned tables (only the columns Python reads).
CREATE TABLE users (
  id VARCHAR(36) PRIMARY KEY, firstName VARCHAR(100), lastName VARCHAR(100),
  phoneNumber VARCHAR(20) UNIQUE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE shops (
  id VARCHAR(36) PRIMARY KEY, name VARCHAR(100), region VARCHAR(50) DEFAULT 'Nairobi', isActive TINYINT(1) DEFAULT 1
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE sales_agents (id VARCHAR(36) PRIMARY KEY, shopId VARCHAR(36))
  ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE devices (
  id VARCHAR(36) PRIMARY KEY, brand VARCHAR(100), model VARCHAR(100), shopId VARCHAR(36),
  status ENUM('AVAILABLE','RESERVED','SOLD','DAMAGED','LOST') DEFAULT 'AVAILABLE',
  rrp DECIMAL(15,2), margin DECIMAL(5,2) DEFAULT 100, stockOwnership ENUM('CELLIPAY','SHOP') DEFAULT 'SHOP',
  loanTermDays INT DEFAULT 365, depositPercentage DECIMAL(5,2) DEFAULT 30,
  depositPromoDiscount DECIMAL(10,2) DEFAULT 0, creditMultiplier DECIMAL(3,1) DEFAULT 1.0
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
CREATE TABLE sales (id VARCHAR(36) PRIMARY KEY) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;