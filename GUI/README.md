# Common Knowledge Base - Web GUI

A React/TypeScript web application that provides a user interface for managing the Common Knowledge Base. The GUI allows users to configure data sources, monitor processing status, manage agencies, and interact with collected knowledge.

## Overview

The GUI serves as the primary interface for CKB administrators and users to:
- Manage agencies and data sources
- Configure scraping parameters and schedules
- Upload files and monitor processing status
- View reports and processing logs
- Search and browse collected content

## Technology Stack

- **React 18.2.0**: Modern React with hooks and functional components
- **TypeScript 4.9.3**: Type-safe development
- **Vite 4.0.0**: Fast build tool and development server
- **SCSS**: Styled components with SASS preprocessing
- **React Query**: Server state management and caching
- **React Router**: Client-side routing
- **Zustand**: Lightweight state management

## Project Structure

```
GUI/
├── src/
│   ├── components/           # Reusable UI components
│   │   ├── DataTable/       # Data grid with sorting/filtering
│   │   ├── FileUploader/    # File upload interface
│   │   ├── FormElements/    # Form input components
│   │   ├── Chat/           # Chat interface components
│   │   └── ...             # Other UI components
│   ├── pages/              # Page-level components
│   │   ├── Agency/         # Agency management
│   │   ├── API/           # API source configuration
│   │   ├── Files/         # File management
│   │   ├── Reports/       # Processing reports
│   │   └── Settings/      # Application settings
│   ├── services/          # API service layer
│   │   ├── agencies.ts    # Agency operations
│   │   ├── sources.ts     # Source management
│   │   ├── files.ts       # File operations
│   │   └── reports.ts     # Report services
│   ├── types/            # TypeScript type definitions
│   ├── hooks/            # Custom React hooks
│   ├── utils/            # Utility functions
│   ├── store/            # State management
│   └── styles/           # SCSS stylesheets
├── public/               # Static assets
├── nginx/               # Nginx configuration
├── translations/        # Internationalization
├── package.json         # Dependencies and scripts
├── tsconfig.json       # TypeScript configuration
├── vite.config.ts      # Vite build configuration
└── Dockerfile          # Container configuration
```

## Features

### Agency Management
- Create, edit, and delete government agencies
- Configure agency metadata and settings
- Monitor agency data processing status
- Manage agency-specific data sources

### Source Configuration
- Add and configure web scraping sources
- Set up API integrations
- Configure scraping intervals and parameters
- Monitor source processing status

### File Management
- Upload files for processing
- View file processing status
- Manage file metadata and exclusions
- Download processed content

### Monitoring and Reports
- View processing reports and logs
- Monitor scraping execution status
- Track error rates and performance metrics
- Generate data export reports

### Search and Browse
- Search across collected content
- Browse files by agency and source
- View file metadata and processing history
- Access cleaned content for review

## Environment Variables

- `VITE_API_URL`: Backend API URL (default: http://localhost:8080)
- `VITE_RUUTER_API_URL`: Ruuter API endpoint
- `VITE_AUTH_URL`: Authentication service URL
- `VITE_FILE_API_URL`: File processing service URL

## Development Scripts

```bash
# Start development server
npm run dev

# Build for production
npm run build

# Type checking and linting
npm run lint

# Code formatting
npm run prettier

# Preview production build
npm run preview
```

## Running the Application

### Development

```bash
# Install dependencies
npm install

# Start development server
npm run dev

# Open browser
open http://localhost:3001
```

### Production Build

```bash
# Build application
npm run build

# Serve with nginx or similar
nginx -c nginx/nginx.conf
```

### Docker

```bash
# Build development image
docker build -f Dockerfile.dev -t gui-dev .

# Build production image  
docker build -t gui-prod .

# Run development container
docker run -p 3001:3001 gui-dev

# Run production container
docker run -p 80:80 gui-prod
```

## Key Components

### DataTable
Reusable data grid component with:
- Sorting and filtering capabilities
- Pagination support
- Custom column rendering
- Search functionality

### FileUploader
File upload interface supporting:
- Multiple file selection
- Progress tracking
- Drag and drop functionality
- File type validation

### FormElements
Comprehensive form component library:
- Input fields with validation
- Select dropdowns and multi-select
- Date pickers and time selectors
- Checkboxes and radio buttons
- Text areas with auto-resize

### Chat Interface
Interactive chat components for:
- Message display and formatting
- Real-time messaging support
- File attachments
- Message history

## API Integration

### Service Layer
- **agencies.ts**: Agency CRUD operations
- **sources.ts**: Source management and configuration
- **files.ts**: File upload and processing operations
- **reports.ts**: Report generation and retrieval

### Authentication
- JWT token-based authentication
- Automatic token refresh
- Role-based access control
- Session management

### Error Handling
- Global error boundary
- Toast notifications for user feedback
- Retry mechanisms for failed requests
- Graceful degradation for service unavailability

## State Management

### Zustand Store
Lightweight state management for:
- User authentication state
- Application configuration
- UI state (modals, notifications)
- Form data persistence

### React Query
Server state management for:
- API response caching
- Background data refetching
- Optimistic updates
- Loading state management

## Internationalization

Support for multiple languages:
- **Estonian (et_EE)**: Primary language
- **English (en)**: Secondary language
- Translation files in `translations/` directory
- Dynamic language switching

## Styling

### SCSS Architecture
- **Variables**: Colors, spacing, typography
- **Mixins**: Reusable style patterns
- **Components**: Component-specific styles
- **Utilities**: Helper classes
- **Base**: Reset and foundational styles

### Design System
- Consistent color palette
- Typography hierarchy
- Spacing system
- Component library
- Responsive breakpoints

## Build Configuration

### Vite Configuration
- **Fast HMR**: Hot module replacement for development
- **TypeScript**: Full TypeScript support
- **SCSS**: SASS preprocessing
- **SVG Imports**: SVG as React components
- **Environment Variables**: Runtime configuration

### TypeScript
- Strict type checking
- Path mapping for clean imports
- Type definitions for all dependencies
- Custom type definitions in `types/`

## Testing

### Mock Service Worker (MSW)
- API mocking for development
- Test data fixtures
- Offline development support
- Consistent test environments

### Testing Setup
- Jest configuration ready
- React Testing Library integration
- Component testing utilities
- API mocking capabilities

## Deployment

### Nginx Configuration
- Static file serving
- API proxy configuration
- Compression and caching
- Security headers

### Container Deployment
- Multi-stage Docker builds
- Development and production images
- Environment-specific configurations
- Health check endpoints

## Security

### Frontend Security
- Content Security Policy (CSP)
- XSS prevention
- Secure cookie handling
- Input sanitization

### Authentication Integration
- JWT token validation
- Automatic logout on token expiry
- Secure token storage
- Role-based UI restrictions

## Performance

### Optimization Features
- Code splitting with dynamic imports
- Lazy loading for routes and components
- Image optimization
- Bundle size optimization
- Progressive loading strategies

### Monitoring
- Error boundary logging
- Performance metrics collection
- User interaction tracking
- API response time monitoring

## Development Guidelines

### Code Standards
- TypeScript strict mode
- ESLint configuration
- Prettier code formatting
- Component composition patterns

### Component Development
- Functional components with hooks
- Custom hooks for reusable logic
- Props interface definitions
- Error boundary implementation

### Testing Strategy
- Unit tests for utility functions
- Component testing with React Testing Library
- Integration tests for user workflows
- API mocking for isolated testing