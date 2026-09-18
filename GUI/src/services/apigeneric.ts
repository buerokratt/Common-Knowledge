import axios, { AxiosError } from 'axios';

// Local development only: points at the mocked Ruuter routes under GET generic/,
// which return static user data and bypass the customJwtCookie guard. Selected in
// main.tsx when REACT_APP_LOCAL is true; never used in a deployed environment.
const instance = axios.create({
  baseURL: import.meta.env.REACT_APP_RUUTER_API_URL + '/generic/',
  headers: {
    Accept: 'application/json',
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
  },
  withCredentials: true,
});

instance.interceptors.response.use(
  (axiosResponse) => axiosResponse,
  (error: AxiosError) => Promise.reject(new Error(error.message))
);

export default instance;
