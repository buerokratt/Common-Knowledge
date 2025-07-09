import { FC, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { MdAttachFile, MdClose, MdErrorOutline } from 'react-icons/md';
import './FileUploader.scss';

export interface FileItem {
  id: string;
  name: string;
  size: number;
  status: 'pending' | 'success' | 'warning' | 'error';
  message?: string;
  url?: string;
}

interface FileUploaderProps {
  files: FileItem[];
  onFilesChange: (files: FileItem[]) => void;
  onFileDelete: (fileId: string) => void;
  maxFileSize?: number; // in bytes
  acceptedTypes?: string;
  multiple?: boolean;
  className?: string;
}

const FileUploader: FC<FileUploaderProps> = ({
  files,
  onFilesChange,
  onFileDelete,
  maxFileSize = 30 * 1024 * 1024, // 30MB default
  acceptedTypes = '.pdf,.doc,.docx,.txt,.html,.htm',
  multiple = true,
  className = '',
}) => {
  const { t } = useTranslation();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [isDragOver, setIsDragOver] = useState(false);

  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
  };

  const validateSingleFile = (file: FileItem): FileItem => {
    // Mock validation logic - you can customize this based on your needs
    if (file.name.toLowerCase().includes('error')) {
      return {
        ...file,
        status: 'error' as const,
        message: t('fileUpload.wrongFormat'),
      };
    } else if (file.name.toLowerCase().includes('warning')) {
      return {
        ...file,
        status: 'warning' as const,
        message: t('fileUpload.fileAlreadyExists'),
        url: file.name,
      };
    } else if (file.size > maxFileSize) {
      return {
        ...file,
        status: 'error' as const,
        message: t('fileUpload.maxSizeExceeded'),
      };
    } else {
      return {
        ...file,
        status: 'success' as const,
      };
    }
  };

  const handleFileSelect = () => {
    fileInputRef.current?.click();
  };

  const handleFileChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const selectedFiles = event.target.files;
    if (!selectedFiles) return;

    const newFiles: FileItem[] = Array.from(selectedFiles).map(
      (file, index) => {
        const fileItem = {
          id: `file-${Date.now()}-${index}`,
          name: file.name,
          size: file.size,
          status: 'pending' as const,
        };
        return validateSingleFile(fileItem);
      }
    );

    const updatedFiles = [...files, ...newFiles];
    onFilesChange(updatedFiles);

    // Reset input value so same file can be selected again
    if (fileInputRef.current) {
      fileInputRef.current.value = '';
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(true);
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);

    const droppedFiles = e.dataTransfer.files;
    if (!droppedFiles) return;

    const newFiles: FileItem[] = Array.from(droppedFiles).map((file, index) => {
      const fileItem = {
        id: `file-${Date.now()}-${index}`,
        name: file.name,
        size: file.size,
        status: 'pending' as const,
      };
      return validateSingleFile(fileItem);
    });

    const updatedFiles = [...files, ...newFiles];
    onFilesChange(updatedFiles);
  };

  const renderFileList = () => {
    if (files.length === 0) return null;

    return (
      <div className="file-uploader__attachments-list">
        {files.map((file) => (
          <div key={file.id} className="file-uploader__attachment-item">
            <div
              className={`file-uploader__attachment ${
                file.status === 'error' || file.status === 'warning'
                  ? 'file-uploader__attachment--error'
                  : ''
              }`}
            >
              <div className="file-uploader__attachment-name">{file.name}</div>
              <div className="file-uploader__attachment-size">
                {formatFileSize(file.size)}
              </div>
              {(file.status === 'error' || file.status === 'warning') && (
                <MdErrorOutline className="file-uploader__attachment-error-icon" />
              )}
              <button
                className="file-uploader__attachment-delete"
                onClick={() => onFileDelete(file.id)}
                type="button"
                aria-label={`Delete ${file.name}`}
              >
                <MdClose />
              </button>
            </div>
            {(file.status === 'error' || file.status === 'warning') && (
              <div className="file-uploader__attachment-error-text">
                {file.message || 'Error occurred'}
              </div>
            )}
          </div>
        ))}
      </div>
    );
  };

  return (
    <div className={`file-uploader ${className}`}>
      <div className="file-uploader__input-container">
        <div
          className={`file-uploader__input ${
            isDragOver ? 'file-uploader__input--drag-over' : ''
          }`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={handleFileSelect}
        >
          <MdAttachFile className="file-uploader__attach-icon" />
          <div className="file-uploader__instructions">
            {t('fileUpload.dropFilesOrClick') ||
              'Drop files here, or click to browse'}
          </div>
        </div>
        <input
          ref={fileInputRef}
          type="file"
          multiple={multiple}
          onChange={handleFileChange}
          style={{ display: 'none' }}
          accept={acceptedTypes}
        />
        <div className="file-uploader__helper-texts">
          <div className="file-uploader__helper-text">
            {t('fileUpload.maxFileSize') || 'Max file size'}{' '}
            {formatFileSize(maxFileSize)}
          </div>
        </div>
      </div>

      {renderFileList()}
    </div>
  );
};

export default FileUploader;
